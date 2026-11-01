# SPDX-License-Identifier: Apache-2.0
import os
import pathlib
import tempfile
import unittest

from pxr import Gf, Sdf, Usd, UsdGeom, Vt


POINTS = Vt.Vec3fArray(
    [Gf.Vec3f(0, 0, 0), Gf.Vec3f(1, 0, 0), Gf.Vec3f(0, 1, 0)]
)
COUNTS = Vt.IntArray([3])
INDICES = Vt.IntArray([0, 1, 2])


def author_directly():
    layer = Sdf.Layer.CreateAnonymous("direct.usda")
    asset = Sdf.PrimSpec(layer, "Asset", Sdf.SpecifierDef, "Xform")
    geo = Sdf.PrimSpec(asset, "geo", Sdf.SpecifierDef, "Scope")
    mesh = Sdf.PrimSpec(geo, "Mesh_0", Sdf.SpecifierDef, "Mesh")
    Sdf.AttributeSpec(mesh, "points", Sdf.ValueTypeNames.Point3fArray).default = POINTS
    Sdf.AttributeSpec(mesh, "faceVertexCounts", Sdf.ValueTypeNames.IntArray).default = COUNTS
    Sdf.AttributeSpec(mesh, "faceVertexIndices", Sdf.ValueTypeNames.IntArray).default = INDICES
    layer.defaultPrim = "Asset"
    layer.pseudoRoot.SetInfo("upAxis", "Y")
    layer.pseudoRoot.SetInfo("metersPerUnit", 1.0)
    return layer


def author_on_stage(stage):
    root = UsdGeom.Xform.Define(stage, "/Asset").GetPrim()
    stage.SetDefaultPrim(root)
    UsdGeom.Scope.Define(stage, "/Asset/geo")
    mesh = UsdGeom.Mesh.Define(stage, "/Asset/geo/Mesh_0")
    mesh.CreatePointsAttr().Set(POINTS)
    mesh.CreateFaceVertexCountsAttr().Set(COUNTS)
    mesh.CreateFaceVertexIndicesAttr().Set(INDICES)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)


def publish_example(destination, fail=False):
    temporary = Usd.Stage.CreateInMemory()
    author_on_stage(temporary)
    if fail:
        raise ValueError("simulated conversion failure")
    destination.TransferContent(temporary.GetRootLayer())
    return temporary


class LayerAuthoringTest(unittest.TestCase):
    def assert_scene(self, layer):
        stage = Usd.Stage.Open(layer)
        self.assertEqual(stage.GetDefaultPrim().GetPath(), Sdf.Path("/Asset"))
        self.assertEqual(UsdGeom.GetStageUpAxis(stage), UsdGeom.Tokens.y)
        self.assertEqual(UsdGeom.GetStageMetersPerUnit(stage), 1.0)
        self.assertTrue(stage.GetPrimAtPath("/Asset/geo").IsA(UsdGeom.Scope))
        mesh = UsdGeom.Mesh.Get(stage, "/Asset/geo/Mesh_0")
        self.assertTrue(mesh)
        self.assertEqual(mesh.GetPointsAttr().Get(), POINTS)
        self.assertEqual(mesh.GetFaceVertexCountsAttr().Get(), COUNTS)
        self.assertEqual(mesh.GetFaceVertexIndicesAttr().Get(), INDICES)

    def test_direct_and_temporary_stage_authoring(self):
        direct = author_directly()
        destination = Sdf.Layer.CreateAnonymous("destination.gltf")
        self.assertEqual(destination.GetFileFormat().formatId, "gltf")
        Sdf.PrimSpec(destination, "Old", Sdf.SpecifierDef, "Xform")
        open_destination = Usd.Stage.Open(destination)
        temporary = publish_example(destination)

        self.assert_scene(direct)
        self.assert_scene(destination)
        self.assertFalse(open_destination.GetPrimAtPath("/Old"))
        self.assertTrue(open_destination.GetPrimAtPath("/Asset/geo/Mesh_0"))
        self.assertEqual(
            direct.ExportToString(), temporary.GetRootLayer().ExportToString()
        )

    def test_discarding_failed_staging_preserves_destination_and_files(self):
        destination = Sdf.Layer.CreateAnonymous("destination.gltf")
        Sdf.PrimSpec(destination, "Existing", Sdf.SpecifierDef, "Xform")
        with tempfile.TemporaryDirectory() as directory:
            previous_directory = os.getcwd()
            try:
                os.chdir(directory)
                with self.assertRaisesRegex(ValueError, "simulated conversion failure"):
                    publish_example(destination, fail=True)
                self.assertTrue(destination.GetPrimAtPath("/Existing"))
                self.assertFalse(destination.GetPrimAtPath("/Asset"))
                self.assertEqual(list(pathlib.Path(directory).iterdir()), [])
            finally:
                os.chdir(previous_directory)


if __name__ == "__main__":
    unittest.main()
