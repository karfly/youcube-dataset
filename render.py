import os
import sys
import time
import argparse
import math

import numpy as np

import bpy
import bmesh
import mathutils


def parse_args():
    argv = sys.argv
    if "--" not in argv:
        argv = []  # as if no args are passed
    else:
        argv = argv[argv.index("--") + 1:]  # get all args after "--"

    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output_dir", type=str, required=True)
    parser.add_argument("-t", "--texture_dir", type=str, required=True)
    parser.add_argument("-i", "--input_obj", type=str, default="cube.obj")
    parser.add_argument("-n", "--n_views", type=int, default=8)

    args = parser.parse_args(argv)

    return args


def clear_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()


def clear_materials():
    all_materials = bpy.data.materials
    for material in all_materials:
        bpy.data.materials.remove(material)


def load_obj(path):
    bpy.ops.import_scene.obj(filepath=path)

def build_cube(size=1.0, location=(0.0, 0.0, 0.0), rotation_euler=(0.0, 0.0, 0.0)):
    cube_mesh = bpy.data.meshes.new('cube')
    cube = bpy.data.objects.new('cube', cube_mesh)

    cube_bmesh = bmesh.new()
    bmesh.ops.create_cube(cube_bmesh, size=size)
    cube_bmesh.to_mesh(cube_mesh)
    cube_bmesh.free()

    cube.location = mathutils.Vector(location)
    cube.rotation_euler = mathutils.Euler(rotation_euler)

    return cube


def point_obj_at(obj, target, roll=0):
    """
    Rotate obj to look at target

    :arg obj: the object to be rotated. Usually the camera
    :arg target: the location (3-tuple or Vector) to be looked at
    :arg roll: The angle of rotation about the axis from obj to target in degres. 

    Based on: https://blender.stackexchange.com/a/5220/12947 (ideasman42)      
    """
    if not isinstance(target, mathutils.Vector):
        target = mathutils.Vector(target)
    loc = obj.location
    # direction points from the object to the target
    direction = target - loc

    quat = direction.to_track_quat('-Z', 'Y')

    # /usr/share/blender/scripts/addons/add_advanced_objects_menu/arrange_on_curve.py
    quat = quat.to_matrix().to_4x4()
    rollMatrix = mathutils.Matrix.Rotation(math.radians(roll), 4, 'Z')

    # remember the current location, since assigning to obj.matrix_world changes it
    loc = loc.to_tuple()
    obj.matrix_world = quat @ rollMatrix
    obj.location = loc


def build_flat_texture_material(texture_path):
    material = bpy.data.materials.new(name='material')
    material.use_nodes = True

    tex_image_node = material.node_tree.nodes.new('ShaderNodeTexImage')
    emission_node = material.node_tree.nodes.new('ShaderNodeEmission')

    tex_image_node.image = bpy.data.images.load(texture_path)

    material.node_tree.links.new(emission_node.inputs['Color'], tex_image_node.outputs['Color'])
    material.node_tree.links.new(material.node_tree.nodes['Material Output'].inputs['Surface'], emission_node.outputs['Emission'])

    return material


def main(args):
    # global setup
    clear_scene()
    root = os.path.dirname(os.path.realpath(__file__))

    collection = bpy.context.collection
    scene = bpy.context.scene

    os.makedirs(args.output_dir, exist_ok=True)

    render_dir = os.path.join(args.output_dir, "render")
    os.makedirs(render_dir, exist_ok=True)

    # setup rendering
    bpy.context.scene.render.engine = 'CYCLES'
    bpy.context.scene.view_layers['View Layer'].use_pass_combined = True
    bpy.context.scene.view_layers['View Layer'].use_pass_uv = True

    scene.render.film_transparent = True
    scene.render.resolution_x = 512
    scene.render.resolution_y = 512

    # setup compositor nodes
    bpy.context.scene.use_nodes = True
    tree = bpy.context.scene.node_tree

    for node in tree.nodes:  # clear default nodes
        tree.nodes.remove(node)

    render_layers_node = tree.nodes.new('CompositorNodeRLayers')
    render_layers_node.location = 0, 0

    output_file_node = tree.nodes.new('CompositorNodeOutputFile')
    output_file_node.location = 500, 0

    output_file_node.file_slots.new('Alpha')
    output_file_node.file_slots.new('UV')

    tree.links.new(output_file_node.inputs['Image'], render_layers_node.outputs['Image'])
    tree.links.new(output_file_node.inputs['Alpha'], render_layers_node.outputs['Alpha'])
    tree.links.new(output_file_node.inputs['UV'], render_layers_node.outputs['UV'])

    # load obj
    load_obj(args.input_obj)
    obj_name = scene.objects.keys()[0]
    obj = scene.objects[obj_name]

    # save uv layout
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    for i in range(len(bpy.data.meshes[obj_name].edges)):
        bpy.data.meshes[obj_name].edges[i].select = True

    # bpy.ops.uv.lightmap_pack()
    bpy.ops.object.mode_set(mode='OBJECT')

    bpy.ops.uv.export_layout(filepath=os.path.join(args.output_dir, "uv_unwrap.png"), mode='PNG', size=(512, 512), opacity=0.25)

    # create the camera
    camera_data = bpy.data.cameras.new('camera')
    camera = bpy.data.objects.new('camera', camera_data)
    collection.objects.link(camera)
    scene.camera = camera

    # render per texture per view
    texture_names = sorted(os.listdir(args.texture_dir))
    for texture_i, texture_name in enumerate(texture_names):
        print("{}/{}".format(texture_i, len(texture_names)))

        clear_materials()

        texture_path = os.path.join(args.texture_dir, texture_name)
        material = build_flat_texture_material(texture_path)

        if obj.data.materials:
            obj.data.materials[0] = material
        else:
            obj.data.materials.append(material)

        for camera_i in range(args.n_views):
            camera_view_dir = os.path.join(render_dir, os.path.splitext(texture_name)[0], "{:06d}".format(camera_i))
            os.makedirs(camera_view_dir, exist_ok=True)

            r = np.random.uniform(2.0, 10.0)
            theta = np.random.uniform(0.0, 2 * np.pi)
            phi = np.random.uniform(0.0, np.pi)
            camera.location = (r * np.cos(theta) * np.sin(phi), r * np.sin(theta) * np.sin(phi), r * np.cos(phi))
            point_obj_at(camera, mathutils.Vector(obj.location))

            camera.location += mathutils.Vector(np.random.uniform(-1.0, 1.0, size=3))

            output_file_node.base_path = camera_view_dir
            bpy.ops.render.render(write_still=True)

    exit()


if __name__ == "__main__":
    args = parse_args()
    print(args)
    main(args)
