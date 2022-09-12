
''' Comment this line for first run if Wand is not installed
import pip
pip.main(['install', 'Wand', '--user'])
# <- do not remove this :) '''
import sys
import bpy
import os

# You must have only one text editor opened when running the script for getting the right path
# That's a little bit tricky but __file__ gives something like
# "x:\blender\project\path\prj.blend\script_file_name.py" when run from blender
# So the only way to get the real path of the current file is to use the path
# from the blender context using `bpy.context.space_data.text.filepath`
tga2dds_dir = os.path.dirname(bpy.context.space_data.text.filepath)
tga2dds_path = os.path.join(tga2dds_dir, 'tga2dds.py')
# Setup the path where wand package is installed here
sys.path.insert(0, 'C:\\Users\\rossHell\\AppData\\Roaming\\Python\\Python39\\site-packages')
# for import tga2dds
sys.path.insert(1, tga2dds_dir)
#sys.path.insert(1, 'C:\\Program Files\\Blender Foundation\\Blender 3.0\\3.0\\scripts\\modules')


import pathlib
import re
import ntpath
import wand
import logging
import subprocess
import dataclasses
import time
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import tga2dds
from importlib import reload
reload(tga2dds)



@dataclasses.dataclass
class BlenderTextureInfo(tga2dds.TextureInfo):
    ''' Add fields and methods to TextureInfo for usage from Blender'''
    node: bpy.types.ShaderNodeTexImage=None
    source: tga2dds.PathInfo=None
    _initial_colorspace_name = 'sRGB'

    # def __init__(self, node:bpy.types.ShaderNodeTexImage):
    #     super().__init__(
    #         tga2dds.PathInfo(bpy.path.abspath(self.node.image.filepath)),)
    #     self.node = node
    #     self._initial_colorspace_name = self.colorspace_name

    def __post_init__(self):
        self.source = tga2dds.PathInfo(
            bpy.path.abspath(self.node.image.filepath))
        self._initial_colorspace_name = self.colorspace_name
        super().__post_init__()

    @property
    def id(self):
        ''' the combination of the full file path and the texture name '''
        return f'{self.source}:{self.texture_name}'


    @property
    def texture_name(self) -> str:
        return self.node.image.name

    @texture_name.setter
    def texture_name(self, new_name:str):
        self.texture_image.name = new_name

    @property
    def texture_image(self) -> bpy.types.Image:
        return self.node.image

    @texture_image.setter
    def texture_image(self, new_image:bpy.types.Image):
        self.node.image = new_image

    @property
    def colorspace_name(self) -> str:
        return self.node.image.colorspace_settings.name

    @colorspace_name.setter
    def colorspace_name(self, new_colorspace_name:str):
        self.texture_image.colorspace_settings.name = new_colorspace_name

    @property
    def initial_colorspace_name(self):
        return self._initial_colorspace_name

@dataclasses.dataclass
class TexturesData:
    textures:List[BlenderTextureInfo] = dataclasses.field(default_factory=list)

    # _by_ids:Dict[str, BlenderTextureInfo] = dataclasses.field(default_factory=dict)
    _by_path:Dict[str, BlenderTextureInfo] = dataclasses.field(default_factory=dict)

    def append(self, bti:BlenderTextureInfo):
        self.textures.append(bti)
        # self._by_ids[bti.texture_image] = bti
        self._by_path[bti.source.path] = bti

    def __iter__(self) -> Iterator[BlenderTextureInfo]:
      return self.textures.__iter__()

    def __len__(self) -> int:
        return len(self.textures)

    def __post_init__(self):
        for t in self.textures:
            # self._by_ids[t.id] = t
            self._by_path[t.source.path] = t

    @property
    def by_path(self) -> Dict[str, BlenderTextureInfo]:
        return self._by_path

    # @property
    # def by_ids(self) -> Dict[str, BlenderTextureInfo]:
    #     ''' dict with id in format "<texture_file_path>:<image_name>"
    #         where image name is the name you can see from blender like
    #         "image.tga.001" '''
    #     return self._by_ids


def get_texture_infos(logger:logging.Logger, ext:str='.tga') -> TexturesData:
    ''' Retrieve all textures files used in current Blender project and matching
        with given extension. '.tga' by default '''
    # TODO: Keep only textures used
    materials = bpy.data.materials

    out = TexturesData()
    for m in materials:
        if m.node_tree is None:
            continue
        for n in m.node_tree.nodes:
            if n.bl_idname == 'ShaderNodeTexImage':
                if n.image.filepath.endswith(ext):
                    ti = BlenderTextureInfo(node=n, output_suffix='_opt')
                    logger.info(f'Image found {ti.texture_name} - file:{ti.source.filename}')
                    logger.info(f'{ti.source.path}')
                    out.append(ti)
                else:
                    logger.debug(f'{ti.source.filename} ignored because not TGA')
                    n.image.reload()

    return out

def convert(textures:TexturesData, logger:logging.Logger, work_dir:Optional[str]=None):
    ''' Convert textures found '''

    if work_dir is None:
        work_dir = os.getcwd()

    # Convert to DDS by folder
    results = []
    nb_tex_replaced = 0
    start = time.time()
    if len(textures) > 0:
        current_path = ''
        paths = {}
        filters = []

        logger.info(f'Processing {len(textures)} textures...')

        # Group all files by folder
        for t in textures.by_path.values():
            # dir_path = str(pathlib.Path(t.source.path).parent.resolve())
            dir_path = t.source.folder
            if current_path != dir_path:
                if len(filters) > 0:
                    if current_path in paths:
                        paths[current_path] = paths[current_path] + filters
                    else:
                        paths[current_path] = filters
                current_path = dir_path
                filters = []
            filters.append(t.source.basename)

        if len(filters) > 0:
            if current_path in paths:
                paths[current_path] = paths[current_path] + filters
            else:
                paths[current_path] = filters

        # Display what we've found
        for p, fs in paths.items():
            logger.debug(p) # Folder path
            logger.debug(fs) # Files to process
            logger.debug('')

        for p, fs in paths.items():

            # '''
            args = tga2dds.Args((p,), shd=True, suffix='_opt', filters=fs, verbose=True)
            converter = tga2dds.Converter(args=args,
                working_dir=work_dir or os.getcwd(), logger=logger)
            res:tga2dds.Results = converter.convert()
            results.append(res)

            logger.info(f'{res.nb_processed} processed !')
            logger.debug(f'{[f"{t.source.filename}->{t.out.filename}" for t in  res.processed]}')
            logger.debug('')
            logger.info(f'{res.nb_skipped} skipped !')
            logger.debug(f'{[f"{t.source.filename}->{t.out.filename}" for t in  res.skipped]}')
            logger.debug('')
            logger.info(f'{res.nb_errors} errors !')
            logger.debug(f'{[f"{t.source.filename}->{t.out.filename}" for t in  res.with_errors]}')
            logger.debug('')

            '''
            filters_args = []
            for f in fs:
                filters_args += ['-f', f]
            suffix = "_opt"
            # Generate DDS
            command_array = ['python', tga2dds_dir]
            # filter only selected files,
            command_array += filters_args
            #with _opt suffix, and shd update/creation enabled, at current path
            command_array += ['-s', suffix, '--shd', f'{p}\\']
            logger.info(f'Calling tga2dds: {command_array}')
            proc = subprocess.Popen(command_array, shell=True)
            (out, err) = proc.communicate()
            # '''

            # TODO: Move code below in dedicated method
            # Replace image names and file names with new files created
            processed_by_filename = {}
            for p in (res.processed + res.skipped):
                processed_by_filename[p.source.path] = p

            for t in textures:
                if t.source.path in processed_by_filename:
                    logger.info(f'updating image name and filepath')
                    logger.info(f'    {t.texture_name} - {t.source.filename}')
                    t.texture_name = t.texture_name.replace(
                        t.source.filename, t.out.filename)
                    t.texture_image.filepath = t.texture_image.filepath.replace(
                        t.source.filename, t.out.filename)
                    t.texture_image.source = 'FILE'
                    t.texture_image.reload()
                    if not t.texture_image.has_data and os.path.exists(t.out.path):
                        logger.info(f'Loading new image {t.out.filename} for texture {t.texture_name}')
                        t.texture_image = bpy.data.images.load(t.out.path)
                        if t.colorspace_name != t.initial_colorspace_name:
                            logger.info(f'Restoring colorspace name to {t.initial_colorspace_name}')
                            t.colorspace_name = t.initial_colorspace_name

                    logger.info(f' -> {t.texture_name} - {t.out.filename}')
                    nb_tex_replaced += 1

    total_res = tga2dds.Results.merge(results)
    logger.info(f'TGA 2 DDS compression terminated !')
    logger.info(f'{total_res.nb_processed} files processed in {time.time() - start:.2f} seconds')
    if(res.total_out_size > 0):
        logger.info(f'Total TGA size: {total_res.total_source_size_string}')
        logger.info(f'Total DDS size: {total_res.total_out_size_string}')
        logger.info(f'Saved space {total_res.saved_string}')
    logger.info(f'{nb_tex_replaced} textures updated in Blender project')
    logger.info(f'')

def main():
    print(f'Python version: {sys.version}')
    print(f'initial os.getcwd() {os.getcwd()}')
    project_path = bpy.path.abspath('//')
    print(f'Blender Project path: {project_path}')
    # Set project path as working directory
    os.chdir(project_path)
    print(f'os.getcwd() {os.getcwd()}')

    logger = tga2dds.create_logger(verbose=True)

    to_convert = get_texture_infos(logger)
    convert(to_convert, logger)

    del logger

if __name__ == "__main__":
    main()