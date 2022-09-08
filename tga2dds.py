
import json
from posixpath import isabs
from typing import List, Sequence, Tuple
from wand.image import Image
import os
import re
import sys
import argparse
import logging
import time
from datetime import datetime

COMPRESSION = 'dxt1'
COMPRESSION_ALPHA = 'dxt3'

nb_files = 0
total_tga_size = 0
total_dds_size = 0
files_pairs = []

# def create_logger() -> logging.Logger:
def create_logger():
    timestamp = datetime.now().strftime("%d%m%Y_%H%M%S")
    # full log
    debug_handler = logging.FileHandler(f'log/{timestamp}_tga2dds_debug.log')
    debug_handler.setLevel(logging.NOTSET)
    debug_formatter = logging.Formatter('[%(asctime)s] {%(pathname)s:%(lineno)d} %(levelname)s - %(message)s')
    debug_handler.setFormatter(debug_formatter)
    file_handler = logging.FileHandler(f'log/{timestamp}_tga2dds.log')
    file_handler.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setLevel(logging.NOTSET)
    logging.basicConfig(
        # filename=f'{timestamp}_tga2dds.log', filemode='a',
        level=logging.NOTSET,
        format= '%(levelname)s - %(message)s',
        datefmt='%H:%M:%S', handlers=(debug_handler, file_handler, console)
    )

    return logging.getLogger('')

logger = create_logger()


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
    help='Process only files matching given patterns. Used as regular expression')
parser.add_argument('-e','--exclude', action='append',
    help='''Opposite of filter, process only files which are not matching given
 patterns. Used as regular expression''')


args:dict = parser.parse_args()
logger.debug(json.dumps(vars(args), indent=2))

compression = COMPRESSION_ALPHA if args.alpha else COMPRESSION
filters = list([re.compile(f, re.IGNORECASE) for f in args.filter or []])
excludes = list([re.compile(f, re.IGNORECASE) for f in args.exclude or []])
def _fn_filter(path:str):
    ''' Filter TGA files only, according to filters'''
    if not path.endswith('.tga'):
        return False
    res = True
    if len(filters) > 0:
        res = False
        for f in filters:
            if f.search(path) is not None:
                res = True
                break
    if res and len(excludes) > 0:
        for e in excludes:
            if e.search(path) is not None:
                res = False
                break
    return res

files = []
logger.info(f'Path {args.path}')

def file_size_to_string(size:int):
    ''' format file size to string '''
    s = size
    mapping = ('', 'K', 'M', 'G', 'T')
    unit = 'o'
    for i in range(len(mapping)):
        v = mapping[i]
        # if s < 1024:
        #     break
        if s < 1024 ** (i+1):
            unit = v + unit
            s = s / (1024 ** i)
            break
    return f'{s:.2f} {unit}'


def get_file_size(path):
    ''' Get file size '''
    return file_size_to_string(os.path.getsize(path))

SHADER_CONTENT = '''bump
{{
	map = {}
}}
specular
{{
	shininess = 6
}}
'''
def replace_in_shaders(path:str, tga_file:str, dds_file:str):
    if args.shd:
        ''' Replace in shd files, generated new shd if necessary'''

        logger.info(f'  Checking file names in shader files...')
        tga_no_ext, ext = os.path.splitext(tga_file)
        dds_no_ext, ext = os.path.splitext(dds_file)
        shd_out_short = tga_no_ext
        if os.path.splitext(tga_file)[0] != dds_no_ext:
            shd_out_short = dds_no_ext
        shd_out_short += '.shd'
        shd_out = os.path.join(path, shd_out_short)
        if not os.path.exists(shd_out):
            # Create missing shader file
            with open(os.path.join(path, shd_out), 'w') as fshd:
                logger.debug(f'    -> "{shd_out_short}" created')
                fshd.write(SHADER_CONTENT.format(dds_file))
        else:
            with open(os.path.join(path, shd_out), 'r') as fshd:
                content = fshd.read()
                if len(content) == 0:
                    fshd.write(SHADER_CONTENT.format(dds_file))
                # TODO: extract file name and replace it even if it's not equal to tga_file
                elif tga_file in content:
                    content = content.replace(tga_file, dds_file)
                    logger.debug(f'    -> "{tga_file}" replaced with "{dds_file}" in "{shd_out_short}"')
                    fshd.write(content)
                elif dds_file not in content:
                    logger.warning(f'{dds_file} not found in {shd_out_short}')
                else:
                    logger.info('    -> OK')
        logger.info('')

def replace_in_track_builder_project(path:str, files:Sequence[Tuple[str, str]]):

    if len(args.trk) > 0:
        trk_path = args.trk
        if not os.path.isabs(trk_path):
            trk_path = os.path.join(path, trk_path)
        if not os.path.exists(trk_path):
            logging.warning(f"{trk_path} doesn't exists, skipped")
        else:
            ''' Replace in track builder project file '''
            prj = {}
            with open(trk_path, 'r') as ftrk:
                logger.info(f'  Checking file names in Track Builder project: {args.trk}')
                prj = json.loads(ftrk.read())

            for (tga_file, dds_file) in files:
                # textures are located under TextureLayers and MaterialLayers sections
                for i in range(len(prj['TextureLayers'])):
                    tl = prj['TextureLayers'][i]
                    if tga_file in tl['Map']:
                        logger.debug(f'    -> {tl["Map"]} replaced by {dds_file} ')
                        prj['TextureLayers'][i]['Map'] = dds_file
                    normal = tl['NormalMap']['Map']
                    if normal is not None and tga_file in normal:
                        logger.debug(f'    -> {normal} replaced by {dds_file} ')
                        prj['TextureLayers'][i]['NormalMap']['Map'] = dds_file

                    if 'Mask' in tl  and tl['Mask'] is not None and tga_file in tl['Mask']:
                        logger.debug(f'    -> {tl["Mask"]} replaced by {dds_file} ')
                        prj['TextureLayers'][i]['Mask'] = dds_file

                for i in range(len(prj['MaterialLayers'])):
                    if tga_file in prj['TextureLayers'][i]['Mask']:
                        logger.debug(f'{prj["TextureLayers"][i]["Mask"]} replaced by {dds_file} ')
                        prj['TextureLayers'][i]['Mask'] = dds_file

            with open(trk_path, 'w') as ftrk:
                json.dump(prj, ftrk, indent=2)

        logger.info('')




def process_files(files:List[str], path:str):
    global total_tga_size, total_dds_size, nb_files
    logger.info(f'Processing folder {path}')
    ''' Convert list of files to dds '''
    for f in files:
        full_path = os.path.join(path, f)
        with Image(filename=full_path) as img:
            logger.info(f'Processing {f}...')
            logger.debug(f'  Image size: {img.size}')
            logger.debug(f'  File size: {get_file_size(full_path)}')
            with img.clone() as i:
                filename, ext = os.path.splitext(full_path)
                shortname, ext = os.path.splitext(f)
                if 'auto' == args.alpha:
                    has_alpha = i.alpha_channel
                    compression = COMPRESSION_ALPHA if has_alpha else COMPRESSION
                i.compression = compression
                suffix = args.suffix or ''
                output = f'{filename}{suffix}.dds'
                if args.lazy and os.path.exists(output):
                    logger.debug(f'{output} skipped as it already exists (lazy)')
                else:
                    # For an unkown reason, the image is flipped vertically when
                    # converted to dds. So we flip the image here for compensating
                    # this "bug"
                    i.flip()
                    try:
                        i.save(filename=output)
                    except Exception as e:
                        logger.error(f'{f} conversion to {output} failed: {e}')

                    if os.path.exists(output):
                        in_size = os.path.getsize(full_path)
                        out_size = os.path.getsize(output)
                        output_short = f'{shortname}{suffix}.dds'
                        logger.info(f'  Compressed successfully to:')
                        logger.info(f'    -> {output_short} ({compression})')
                        logger.debug(f'    Size {file_size_to_string(out_size)} ({out_size/in_size*100:.2f}%)')
                        logger.debug('')
                        total_tga_size += in_size
                        total_dds_size += out_size
                        replace_in_shaders(path, f, output_short)
                        files_pairs.append((f, output_short))
                        nb_files += 1
                    else:
                        logger.error((f'DDS file {output} not found on disk after convertion'))

    replace_in_track_builder_project(path, files_pairs)


logger.info(f'Start compressing files.')
logger.info(f' Alpha mode {args.alpha}')
start = time.time()

for p in args.path:
    logger.debug(p)
    if p.endswith('"'):
        p = p.replace('"', '')
    files = list(filter(_fn_filter, os.listdir(p)))
    process_files(files, p)

logger.info(f'TGA 2 DDS compression terminated !')
logger.info(f'{nb_files} files processed in {time.time() - start:.2f} seconds')
if(total_tga_size > 0):
    logger.info(f'Total TGA size: {file_size_to_string(total_tga_size)}')
    logger.info(f'Total DDS size: {file_size_to_string(total_dds_size)}')
    saved = total_tga_size-total_dds_size
    logger.info(f'Saved space {file_size_to_string(saved)} - {saved/total_tga_size*100:.2f}%')
logger.info(f'')




