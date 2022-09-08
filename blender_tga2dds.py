
import pip
# Uncomment this for first run if Wand is not installed
#pip.main(['install', 'Wand', '--user', '--force-reinstall'])
import sys
# Setup the path where wand package is installed here
sys.path.insert(0, 'C:\\Users\\rossHell\\AppData\\Roaming\\Python\\Python39\\site-packages')
#sys.path.insert(0, 'E:\\Game\\MxBikes\\Helpers\\TgaToDDS\\')
tga2dds_path = 'E:\\Game\\MxBikes\\Helpers\\TgaToDDS\\tga2dds.py'

import bpy
import os
import pathlib
import re
import ntpath
import wand
import logging
import subprocess
logger = logging.getLogger('')
logger.setLevel(logging.NOTSET)

print(f'Python version: {sys.version}')
print(os.getcwd())
project_path = bpy.path.abspath('//')
print(f'Project path: {project_path}')
os.chdir(project_path)

materials = bpy.data.materials

textures = []
textures_by_filename = {}
textures_nodes_by_filename = {}
for m in materials:
    if m.node_tree is None:
        continue
    for n in m.node_tree.nodes:
        if n.bl_idname == 'ShaderNodeTexImage':
            img = n.image
            color_space = img.colorspace_settings.name
            # We could use "color_space" information for discriminating textures with alpha (specular)
            # and the others

            if img.filepath.endswith('.tga'):
                logger.info(f'Image found {img.name} - {color_space}')
                logger.info(f'{img.filepath}')
                textures.append(img)
                fp = bpy.path.abspath(img.filepath)
                textures_nodes_by_filename[ntpath.basename(bpy.path.abspath(img.filepath))] = n
            else:
                logger.debug(f'{img.name} ignored because not TGA')
                img.reload()
                img.gl_load()



if len(textures) > 0:
    current_path = ''
    paths = {}
    filters = []
    for img in textures:
        fp = bpy.path.abspath(img.filepath)
        if fp.startswith('//'):
            fp = fp.replace('//', '')
        dir_path = str(pathlib.Path(os.path.join(os.getcwd(), fp)).parent.resolve())
        if current_path != dir_path:
            if len(filters) > 0:
                if current_path in paths:
                    paths[current_path] = paths[current_path] + filters
                else:
                    paths[current_path] = filters
#                paths[current_path] = paths.get(current_path, []) + filters
            current_path = dir_path
            filters = []
        fn = ntpath.basename(fp)
        textures_by_filename[fn] = img
        filters.append(fn)

    logger.debug(textures_by_filename)
    logger.debug(textures_nodes_by_filename)

    if len(filters) > 0:
        if current_path in paths:
            paths[current_path] = paths[current_path] + filters
        else:
            paths[current_path] = filters


    # Display what we've found
    for p, fs in paths.items():
        logger.debug(p)
        logger.debug(fs)
        logger.debug('')

    # Convert to DDS by folder
    for p, fs in paths.items():
        filters_args = []
        for f in fs:
            filters_args += ['-f', f]
#        filter_string = ' -f ' + ' -f '.join(fs)
        suffix = "_opt"

        # Generate DDS
        command_array = ['python', tga2dds_path]
        # filter only selected files,
        command_array += filters_args
        #with _opt suffix, and shd update/creation enabled, at current path
        command_array += ['-s', suffix, '--shd', f'{p}\\']
        logger.info(f'Calling tga2dds: {command_array}')
#        p = subprocess.Popen(tga2dds_command, shell=True)
        proc = subprocess.Popen(command_array, shell=True)
        (out, err) = proc.communicate()

        if err is not None:
            logger.error(f'tga2dds has failed: {err}. Aborting')
            # abort() doesn't exists so it throws an exception and... abort the execution :)
            abort()

        # Replace image names and file names with new files created
        to_replace = {}
#        list(f'{[os.path.splitext(fn)[0]}{suffix}.dds' for fn in fs])
        for f in fs:
            dds_filename = f'{os.path.splitext(f)[0]}{suffix}.dds'
            to_replace[f] = dds_filename

            logger.debug(f'{f} -> {dds_filename}')

            if f in textures_by_filename:
                img = textures_by_filename[f]
                node = textures_nodes_by_filename[f]
                logger.info(f'updating image name and filepath')
                logger.info(f'    {img.name} - {img.filepath}')
                img.name = img.name.replace(f, dds_filename)
                img.filepath = img.filepath.replace(f, dds_filename)
                img.source = 'FILE'
                img.reload()
                if not img.has_data:
                    node.image = bpy.data.images.load(os.path.join(p, dds_filename))
                logger.info(f' -> {img.name} - {img.filepath}')

