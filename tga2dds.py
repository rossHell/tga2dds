
import dataclasses
from distutils import extension
import json
import ntpath
from posixpath import isabs
from typing import List, Optional, Sequence, Tuple
from wand.image import Image
import os
import re
import sys
import argparse
import logging
import time
from datetime import datetime

DEFAULT_COMPRESSION = ('dxt1', 'dxt3')

RE_REPLACE_SHD_FN = re.compile(r'\bmap\b\s*=\s*([\w\s\.]+)\s*$')
SHADER_CONTENT = '''bump
{{
	map = {}
}}
specular
{{
	shininess = 6
}}
'''

class MakeFileHandler(logging.FileHandler):
    def __init__(self, filename, mode='a', encoding=None, delay=0):
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        logging.FileHandler.__init__(self, filename, mode, encoding, delay)

class Args:
    def __init__(self, paths:Sequence[str], alpha:str='auto',
            compression:Sequence[str]=DEFAULT_COMPRESSION, lazy:bool=False,
            shd:bool=False, trk:Optional[str]=None, suffix:Optional[str]=None,
            filters:Optional[Sequence[str]]=None, excludes:Optional[Sequence[str]]=None,
            ext_src='tga', ext_out='dds', verbose:Optional[bool]=None):
        self.paths:List[str] = paths
        self.alpha:str = alpha
        self.compression:Sequence[str] = compression
        self.lazy:bool = lazy
        self.shd:bool = shd
        self.trk:str = trk or ''
        self.suffix:str = suffix or ''
        self.filters:Sequence[re.Pattern] = tuple(
            [re.compile(f, re.IGNORECASE) for f in filters or []])
        self.excludes:Sequence[re.Pattern] = tuple(
            [re.compile(f, re.IGNORECASE) for f in excludes or []])
        self.ext_src = ext_src
        self.ext_out = ext_out
        self.verbose = verbose or False

    @classmethod
    def from_namespace(cls, args:argparse.Namespace) -> 'Args':
        ''' Init Tga2DdsArgs from argparse.Namespace '''
        compression = args.compression.split(' ')
        if len(compression) < 2:
            compression = DEFAULT_COMPRESSION

        return Args(
            paths=args.path, alpha=args.alpha, compression=compression,
            lazy=args.lazy, shd=args.shd, tkr=args.trk, suffix=args.suffix,
            filters=args.filter, excludes=args.exclude, ext_src=args.ext_src,
            ext_out=args.ext_out, verbose=args.verbose
        )

@dataclasses.dataclass
class PathInfo:
    path:str
    def __post_init__(self):
        self.path = os.path.realpath(self.path)
    @property
    def folder(self):
        return os.path.dirname(self.path)

    @property
    def filename(self):
        # return os.path.basename(self.path)
        return ntpath.basename(self.path)

    @property
    def basename(self):
        return os.path.splitext(self.filename)[0]

    @property
    def exists(self):
        return os.path.exists(self.path)

@dataclasses.dataclass
class TextureInfo:
    ''' Store info about textures to be converted and provided helper methods '''
    source:PathInfo
    # args:Args
    ext_src:str = '.tga'
    ext_out:str = '.dds'
    output_suffix:str = ''
    _out:PathInfo = None

    def __post_init__(self):
        self._out = PathInfo(os.path.join(
            self.source.folder,
            f'{self.source.basename}{self.output_suffix}{self.ext_out}'
        ))

    @property
    def path(self):
        return self.source.folder

    @property
    def out(self):
        return self._out

    @property
    def is_valid(self) -> bool:
        ''' Indicates if the extension of the source file corresponds to the
        extension expected by args.ext_src '''
        return self.ext_src == f'.{os.path.splitext(self.source.filename)[1]}'

# Class for output results
@dataclasses.dataclass
class Results:
    total_source_size:int = 0
    total_out_size:int = 0
    processed:List[TextureInfo]=dataclasses.field(default_factory=list)
    skipped:List[TextureInfo]=dataclasses.field(default_factory=list)
    with_errors:List[TextureInfo]=dataclasses.field(default_factory=list)

    def __iadd__(self, other:'Results'):
        self.total_source_size += other.total_source_size
        self.total_out_size += other.total_out_size
        self.processed += other.processed
        self.skipped += other.skipped
        self.with_errors += other.with_errors

    @staticmethod
    def merge(results:Sequence['Results']) -> 'Results':
        ''' Merge results data together '''
        new_res = Results()
        for r in results:
            new_res.total_source_size += r.total_source_size
            new_res.total_out_size += r.total_out_size
            new_res.processed += r.processed
            new_res.skipped += r.skipped
            new_res.with_errors += r.with_errors
        return new_res

    @property
    def saved(self) -> int:
        return self.total_source_size - self.total_out_size

    @property
    def saved_string(self) -> str:
        return f'{file_size_to_string(self.saved)} ({self.total_out_size/self.total_source_size*100:.2f}%)'

    @property
    def nb_processed(self) -> int:
        return len(self.processed)

    @property
    def nb_skipped(self) -> int:
        return len(self.skipped)

    @property
    def nb_errors(self) -> int:
        return len(self.with_errors)

    @property
    def total_source_size_string(self) -> str:
        return file_size_to_string(self.total_source_size)

    @property
    def total_out_size_string(self) -> str:
        return file_size_to_string(self.total_out_size)




# def create_logger() -> logging.Logger:
def create_logger(verbose:bool=False):
    timestamp = datetime.now().strftime("%d%m%Y_%H%M%S")

    l = logging.getLogger('tga2dds')

    if not l.hasHandlers():
        # full log
        debug_handler = MakeFileHandler(f'log/{timestamp}_tga2dds_debug.log')
        debug_handler.setLevel(logging.NOTSET)
        debug_formatter = logging.Formatter('[%(asctime)s] {%(pathname)s:%(lineno)d} %(levelname)s - %(message)s')
        debug_handler.setFormatter(debug_formatter)

        file_handler = MakeFileHandler(f'log/{timestamp}_tga2dds.log')
        file_handler.setLevel(logging.INFO)
        formatter = logging.Formatter(fmt= '[%(levelname)s] %(message)s', datefmt='%H:%M:%S')
        file_handler.setFormatter(formatter)

        console = logging.StreamHandler(sys.stdout)
        console.setLevel(logging.NOTSET if verbose else logging.INFO)
        console.setFormatter(formatter)

        l.addHandler(debug_handler)
        l.addHandler(file_handler)
        # l.addHandler(console)
    return l

def command_line(logger:logging.Logger) -> Args:
    parser = argparse.ArgumentParser(
        description='Convert TGA images to DDS')

    parser.add_argument('path', nargs='+',
        help='Path of file(s) or folder(s) containing files to convert')
    # parser.add_argument('-a','--alpha', action='store_true',
    #     help='Compress from image with alpha (dxt3)')
    # TODO: change choice with dxt1, dxt3, ...
    # TODO: split option to alpha and compression, allow to diable alpha channel
    parser.add_argument('-a','--alpha',  choices=['on', 'off', 'auto'], default='auto',
        const='auto', nargs='?', help='''On -> dxt3, off -> dxt1, auto -> enable
        alpha mode if alpha-channel if found in image, disable otherwise ''')
    parser.add_argument('-c','--compression', action='append',
        help='''Type of compression to be used for non-alpha and alpha mode,
        provided as two values in the same order. Default are dxt1 (Non-alpha)
        and dxt3 (Alpha). Example: -c dxt1 dxt3''')
    parser.add_argument('-l','--lazy', action='store_true',
        help='Lazy mode, does not create dds file if it already exists')
    parser.add_argument('--shd', action='store_true',
        help='''Replace <file_name>.tga by <output_file_name>.dds in corresponding shd file, if found.
    Create automatically a new shd file in case dds filename is different than tga's one''')
    parser.add_argument('--trk', nargs='?', const='', default='',
        help='''Replace <file_name>.tga by <output_file_name>.dds in given trk
    (Resolute Track Builder Helper project) file. Must exists''')
    parser.add_argument('-s','--suffix', nargs='?', const='',
        help='Suffix to be added to DDS image output file name')
    parser.add_argument('-f','--filter', action='append',
        help='''Process only files matching given patterns. Can be combined with
        exclude option. Used as regular expression''')
    parser.add_argument('-e','--exclude', action='append',
        help='''Process only files which are not matching given patterns. Can
        be combined with filter option. Used as regular expression.''')
    parser.add_argument('--ext-src', '--extension-source', action='append',
        help='''Extension for source file. "tga" by default, but can be any other
        format supported by Wand. Can be specified multiple times''')
    parser.add_argument('--ext-out', '--extension-output', nargs='?', const='dds',
        help='''Extension for output file. "dds" by default, but can be any other
        format supported by Wand''')
    parser.add_argument('-v','--verbose', action='store_true',
        help='Enable verbose mode')

    args = parser.parse_args()
    logger.debug(json.dumps(vars(args), indent=2))
    return Args.from_namespace(args)


# files = []

def file_size_to_string(size:int):
    ''' format file size to string in humand readable form like 2.34Ko, 8Mo, etc.. '''
    s = size
    mapping = ('', 'K', 'M', 'G', 'T', 'P', 'E', 'Z', 'V')
    unit = 'o'
    for i in range(len(mapping)):
        if s < 1024 ** (i+1):
            unit = mapping[i] + unit
            s = s / (1024 ** i)
            break
    return f'{s:.2f} {unit}'.rstrip('0').rstrip('.')

def get_file_size(path):
    ''' Get file size '''
    return file_size_to_string(os.path.getsize(path))

class Converter:

    def __init__(self, args:Args, working_dir:Optional[str]=None,
        logger:Optional[logging.Logger]=None) -> None:

        self.logger = logger or create_logger(args.verbose)
        self.working_dir = working_dir or os.getcwd()
        self.args = args

    def _fn_filter(self, path:str):
        ''' Filter input files matching with expected source extension only
        (arg.ext_src), according to filters'''
        ext = self.args.ext_src
        if not path.endswith('tga'):
            return False
        res = True
        if len(self.args.filters) > 0:
            res = False
            for f in self.args.filters:
                if f.search(path) is not None:
                    res = True
                    break
        if res and len(self.args.excludes) > 0:
            for e in self.args.excludes:
                if e.search(path) is not None:
                    res = False
                    break
        return res

    def replace_in_shaders(self, texture:TextureInfo, shader_content=SHADER_CONTENT):
        ''' Replace filename in shd files, generate new shd if necessary'''
        # shd option must be enabled
        if self.args.shd:
            self.logger.info(f'  Checking file names in shader files...')
            fin = texture.source
            fout = texture.out
            # Initialize shader file with same name as out file
            shd_out_short = f'{fout.basename}.shd'
            shd_out = os.path.join(texture.path, shd_out_short)
            if not os.path.exists(shd_out):
                # Create missing shader file
                with open(shd_out, 'w') as fshd:
                    self.logger.debug(f'    -> "{shd_out_short}" created')
                    fshd.write(shader_content.format(fout.path))
            else:
                # Update
                with open(shd_out, 'r+') as fshd:
                    content:str = fshd.read()
                    # Empty file
                    if len(content) == 0:
                        fshd.write(shader_content.format(fout.path))
                    # Already up to date
                    elif fout.filename in content:
                        self.logger.info('    -> OK')
                    # Replace any existing filename with new output filename
                    elif RE_REPLACE_SHD_FN.match(content):
                        content = RE_REPLACE_SHD_FN.sub('map = \1', fout.filename)
                        self.logger.debug(f'''    -> "{fin.filename}" replaced with
                            "{fout.filename}" in "{shd_out_short}"''')
                        fshd.write(content)
                    elif fout.filename not in content:
                        self.logger.warning(f'''{fout.filename} not found in {shd_out_short}
                            or format is not valid''')
            self.logger.info('')

    def replace_in_track_builder_project(self, textures:Sequence[TextureInfo]):
        if len(self.args.trk) > 0:
            trk_path = self.args.trk
            if not os.path.isabs(trk_path):
                trk_path = os.path.join(self.working_dir, trk_path)
            if not os.path.exists(trk_path):
                logging.warning(f"{trk_path} doesn't exists, skipped")
            else:
                ''' Replace in track builder project file '''
                prj = {}
                with open(trk_path, 'r') as ftrk:
                    self.logger.info(f'  Checking file names in Track Builder project: {self.args.trk}')
                    prj = json.loads(ftrk.read())

                for t in textures:
                    fout = t.source.filename
                    fin = t.out.filename
                    # textures are located under TextureLayers and MaterialLayers sections
                    for i in range(len(prj['TextureLayers'])):
                        tl = prj['TextureLayers'][i]
                        if fout in tl['Map']:
                            self.logger.debug(f'    -> {tl["Map"]} replaced by {fin} ')
                            prj['TextureLayers'][i]['Map'] = fin
                        normal = tl['NormalMap']['Map']
                        if normal is not None and fout in normal:
                            self.logger.debug(f'    -> {normal} replaced by {fin} ')
                            prj['TextureLayers'][i]['NormalMap']['Map'] = fin

                        if 'Mask' in tl  and tl['Mask'] is not None and fout in tl['Mask']:
                            self.logger.debug(f'    -> {tl["Mask"]} replaced by {fin} ')
                            prj['TextureLayers'][i]['Mask'] = fin

                    for i in range(len(prj['MaterialLayers'])):
                        if fout in prj['TextureLayers'][i]['Mask']:
                            self.logger.debug(f'{prj["TextureLayers"][i]["Mask"]} replaced by {fin} ')
                            prj['TextureLayers'][i]['Mask'] = fin

                # Write back Track Builder config once replacements are done
                with open(trk_path, 'w') as ftrk:
                    json.dump(prj, ftrk, indent=2)

            self.logger.info('')

    def convert(self) -> Results:

        self.logger.info(f'Start compressing files.')
        self.logger.info(f' Alpha mode {self.args.alpha}')
        start = time.time()

        res = Results()
        for path in self.args.paths:
            self.logger.debug(path)
            if path.endswith('"'):
                path = path.replace('"', '')

            # Get list of files to process
            files = list(filter(self._fn_filter, os.listdir(path)))
            textures = list([
                TextureInfo(source=PathInfo(
                    os.path.join(path, f)),
                    output_suffix='_opt'
                )
                for f in files
            ])
            self.logger.info(f'Processing folder {path}')
            ''' Convert list of files to dds '''
            for texture in textures:
                pin = texture.source
                self.logger.info(f'opening image {pin.path}')
                with Image(filename=pin.path) as img:
                    self.logger.info(f'Processing {pin.filename}...')
                    self.logger.debug(f'  Image size: {img.size}')
                    self.logger.debug(f'  File size: {get_file_size(pin.path)}')
                    with img.clone() as i:
                        if 'auto' == self.args.alpha:
                            has_alpha = i.alpha_channel
                            compression = self.args.compression[1] if has_alpha else self.args.compression[0]
                        # force off only ?
                        elif 'off' == self.args.alpha:
                            i.alpha_channel = False
                        i.compression = compression
                        pout = texture.out
                        output = pout.path
                        if self.args.lazy and pout.exists():
                            self.logger.debug(f'{pout.filename} skipped as it already exists (lazy)')
                            res.skipped.append(texture)
                        else:
                            # For an unkown reason, the image is flipped vertically when
                            # converted to dds. So we flip the image here for compensating
                            # this "bug"
                            i.flip()
                            try:
                                i.save(filename=output)
                                self.logger.debug(f'{output} written successfully !')
                            except Exception as e:
                                self.logger.error(f'{pin.filename} conversion to {output} failed: {e}')

                            if os.path.exists(output):
                                in_size = os.path.getsize(pin.path)
                                out_size = os.path.getsize(output)
                                self.logger.info(f'  Compressed successfully to:')
                                self.logger.info(f'    -> {pout.filename} ({compression})')
                                self.logger.debug(f'    Size {file_size_to_string(out_size)} ({out_size/in_size*100:.2f}%)')
                                self.logger.debug('')
                                res.total_source_size += in_size
                                res.total_out_size += out_size
                                self.replace_in_shaders(texture)
                                res.processed.append(texture)
                            else:
                                self.logger.error((f'DDS file {output} not found on disk after convertion'))
                                res.with_errors.append(texture)

            self.replace_in_track_builder_project(res.processed+res.skipped)
            self.logger.info(f'TGA 2 DDS compression terminated !')
            self.logger.info(f'{res.nb_processed} files processed in {time.time() - start:.2f} seconds')
            if(res.total_out_size > 0):
                self.logger.info(f'Total TGA size: {res.total_out_size_string}')
                self.logger.info(f'Total DDS size: {res.total_source_size_string}')
                self.logger.info(f'Saved space {res.saved_string}')
            self.logger.info(f'')

        return res

def main():
    ''' '''
    args = command_line(create_logger())
    c = Converter(args, args.path[0])
    c.convert()

if __name__ == "__main__":
    ''' Entry point '''
    main()