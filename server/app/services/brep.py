"""Exact STEP geometry from a B-rep kernel (OpenCascade via `cadquery-ocp`).

Imported lazily by cad_parser: OCP is a large optional dependency, and the text
parser is used when it is not installed.
"""

import math
import tempfile
from dataclasses import dataclass

import numpy as np
from OCP.Bnd import Bnd_Box
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp, BRepGProp_Face
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.gp import gp_Pnt, gp_Vec
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.Message import Message, Message_Gravity
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

# Keep the reader's progress and warnings out of the server log.
for _p in Message.DefaultMessenger_s().Printers():
    _p.SetTraceLevel(Message_Gravity.Message_Fail)

FULL_TURN = 2 * math.pi * 0.95  # faces of one cylinder covering ≥ this are a full bore/diameter


@dataclass
class CylFace:
    loc: np.ndarray       # a point on the axis
    axis: np.ndarray      # unit direction
    radius: float
    span: float           # angular extent, radians
    concave: bool         # material outside the cylinder: a hole / bore


@dataclass
class Brep:
    bbox: list[float]     # sorted ascending, mm
    volume_mm3: float
    area_mm2: float
    face_count: int
    cylinders: list[CylFace]


def read_step(data: bytes) -> Brep | None:
    """Exact geometry of the solid in a STEP file, or None if it has none."""
    with tempfile.NamedTemporaryFile(suffix=".step") as tmp:
        tmp.write(data)
        tmp.flush()
        reader = STEPControl_Reader()
        if reader.ReadFile(tmp.name) != IFSelect_RetDone:
            return None
        reader.TransferRoots()
        shape = reader.OneShape()
    if shape.IsNull():
        return None

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    volume = abs(props.Mass())
    if volume <= 0:
        return None  # surfaces only, no closed solid
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, props)
    area = props.Mass()

    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    lo, hi = box.CornerMin(), box.CornerMax()
    bbox = sorted([hi.X() - lo.X(), hi.Y() - lo.Y(), hi.Z() - lo.Z()])

    faces, cylinders = 0, []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        faces += 1
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() == GeomAbs_Cylinder:
            cylinders.append(_cyl_face(face, surf))
        explorer.Next()
    return Brep(bbox, volume, area, faces, cylinders)


def _cyl_face(face, surf) -> CylFace:
    cyl = surf.Cylinder()
    ax = cyl.Axis()
    loc = np.array([ax.Location().X(), ax.Location().Y(), ax.Location().Z()])
    axis = np.array([ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()])
    u0, u1 = surf.FirstUParameter(), surf.LastUParameter()
    v0, v1 = surf.FirstVParameter(), surf.LastVParameter()
    p, n = gp_Pnt(), gp_Vec()
    BRepGProp_Face(face).Normal((u0 + u1) / 2, (v0 + v1) / 2, p, n)  # respects face orientation
    pt = np.array([p.X(), p.Y(), p.Z()])
    radial = (pt - loc) - np.dot(pt - loc, axis) * axis
    concave = float(np.dot([n.X(), n.Y(), n.Z()], radial)) < 0
    return CylFace(loc, axis, cyl.Radius(), u1 - u0, concave)


def _coaxial(a: CylFace, b: CylFace, tol: float = 1e-3) -> bool:
    if abs(abs(float(np.dot(a.axis, b.axis))) - 1) > 1e-6:
        return False
    d = b.loc - a.loc
    return float(np.linalg.norm(d - np.dot(d, a.axis) * a.axis)) <= tol * max(1.0, a.radius)


def group_cylinders(cyls: list[CylFace]) -> list[list[CylFace]]:
    """Faces of the same cylinder (coaxial, same radius, same side) grouped together.

    CAD kernels write a bore as one face or as two half-faces; grouping makes the
    count independent of that.
    """
    groups: list[list[CylFace]] = []
    for c in cyls:
        for g in groups:
            h = g[0]
            if h.concave == c.concave and math.isclose(h.radius, c.radius, rel_tol=1e-4) and _coaxial(h, c):
                g.append(c)
                break
        else:
            groups.append([c])
    return groups


def full_cylinders(cyls: list[CylFace], concave: bool) -> list[list[CylFace]]:
    """Complete bores (concave) or outer diameters (convex); fillets and partial arcs are excluded."""
    return [
        g for g in group_cylinders(cyls)
        if g[0].concave == concave and sum(c.span for c in g) >= FULL_TURN
    ]
