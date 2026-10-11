"""Regenerate the STEP reference parts in this folder (needs cadquery-ocp).

    .venv/bin/python tests/fixtures/make_fixtures.py

The files are committed so the text-parser fallback can be tested without OCP.
"""

import math
from pathlib import Path

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec
from OCP.IFSelect import IFSelect_RetDone
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

HERE = Path(__file__).parent
Z = gp_Dir(0, 0, 1)


def box(x, y, z, dx, dy, dz):
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), dx, dy, dz).Shape()


def cyl(x, y, z, r, h, d=Z, angle=None):
    ax = gp_Ax2(gp_Pnt(x, y, z), d)
    m = BRepPrimAPI_MakeCylinder(ax, r, h, angle) if angle else BRepPrimAPI_MakeCylinder(ax, r, h)
    return m.Shape()


def cut(a, *bs):
    for b in bs:
        a = BRepAlgoAPI_Cut(a, b).Shape()
    return a


def fuse(a, *bs):
    for b in bs:
        a = BRepAlgoAPI_Fuse(a, b).Shape()
    return a


def clean(s):
    u = ShapeUpgrade_UnifySameDomain(s, True, True, True)
    u.Build()
    return u.Shape()


def write(shape, name):
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    assert w.Write(str(HERE / name)) == IFSelect_RetDone, name


def hex_bar(across_flats, length):
    r = across_flats / math.sqrt(3)  # circumradius
    poly = BRepBuilderAPI_MakePolygon()
    for i in range(6):
        a = math.radians(60 * i)
        poly.Add(gp_Pnt(r * math.cos(a), r * math.sin(a), 0))
    poly.Close()
    face = BRepBuilderAPI_MakeFace(poly.Wire()).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, length)).Shape()


def l_bracket(t=2.0, ri=2.0, leg_a=50.0, leg_b=30.0, width=40.0):
    """Single 90° bend: horizontal leg along +X, vertical leg along +Z."""
    ro = ri + t
    # bend centre at (0, 0, ro); horizontal leg z∈[0, t], vertical leg x∈[-ro, -ri]
    horiz = box(0, 0, 0, leg_a, width, t)
    vert = box(-ro, 0, ro, t, width, leg_b)
    y = gp_Dir(0, 1, 0)
    ring = cut(cyl(0, 0, ro, ro, width, y, math.pi * 2), cyl(0, 0, ro, ri, width, y))
    quarter = cut(ring, box(-ro - 1, -1, ro, 2 * ro + 2, width + 2, ro + 1), box(0, -1, -1, ro + 1, width + 2, 2 * ro + 2))
    return clean(fuse(horiz, quarter, vert))


if __name__ == "__main__":
    write(box(0, 0, 0, 100, 60, 40), "box_100x60x40.step")
    write(cyl(0, 0, 0, 25, 120), "cylinder_d50x120.step")
    write(cyl(0, 0, 0, 50, 20), "disc_d100x20.step")
    write(
        clean(cut(box(0, 0, 0, 120, 80, 25), *(cyl(x, 40, -1, 5, 27) for x in (30, 60, 90)))),
        "block_120x80x25_3holes.step",
    )
    write(clean(cut(box(0, 0, 0, 150, 100, 50), box(10, 10, 10, 130, 80, 41))), "housing_150x100x50.step")
    write(
        clean(cut(box(0, 0, 0, 100, 100, 30), *(cyl(x, y, -1, 11, 32) for x in (25, 75) for y in (25, 75)))),
        "plate_100x100x30_4holes.step",
    )
    write(hex_bar(30, 100), "hex_af30x100.step")
    write(l_bracket(), "l_bracket_1bend.step")
    print("written to", HERE)
