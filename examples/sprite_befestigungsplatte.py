# SPDX-License-Identifier: MIT
"""
Sprite Befestigungsplatte — rebuilt from the PDF drawing.

Coordinate system (mm): origin at the bottom-left of the bounding box,
+X right, +Y up, +Z thickness. Matches the front view of the drawing.

Locked from the drawing (do not 'eyeball' these):
  envelope        65.60 x 35.00
  tab width       47.50   (so tab starts at x=18.10)
  tab height      15.15   (shoulder at y=19.85)
  two top holes   Ø3.20 on 31.00 spacing, 8.35 down from top
  left hole       Ø5.10 at y=8.05, 40.00 left of the right large hole
  U-slot          18.00 wide, shares the large-hole centre-line
  plate           2.00 thick; centre island 4.50 thick
  right notch     8.00 in from the tab's right edge
"""

import FreeCAD as App
import Part

# --- drawing values -------------------------------------------------------
W = 65.60
H = 35.00
TAB_W = 47.50
TAB_H = 15.15
TAB_HOLE = 3.20
TAB_HOLE_SPAN = 31.00
TAB_HOLE_FROM_TOP = 8.35
EAR_HOLE = 5.10
EAR_HOLE_Y = 8.05
LARGE_HOLE_SPAN = 40.00
U_W = 18.00
U_TO_RIGHT_HOLE = 20.50  # left of U → right large-hole CL
NOTCH = 8.00
T_PLATE = 2.00
T_BOSS = 4.50

# derived
tab_x0 = W - TAB_W          # 18.10
shoulder_y = H - TAB_H      # 19.85
tab_y_holes = H - TAB_HOLE_FROM_TOP  # 26.65
x_right = W
x_notch = W - NOTCH         # 57.60
x_r_hole = x_notch - 10.80  # 46.80  (10.80 from CL to inner-right)
x_l_hole = x_r_hole - LARGE_HOLE_SPAN  # 6.80
x_u0 = x_r_hole - U_TO_RIGHT_HOLE      # 26.30
x_u1 = x_u0 + U_W                      # 44.30
x_uc = (x_u0 + x_u1) / 2.0
# top holes centred on the tab
tab_cx = tab_x0 + TAB_W / 2.0
x_th_l = tab_cx - TAB_HOLE_SPAN / 2.0
x_th_r = tab_cx + TAB_HOLE_SPAN / 2.0

# undocumented chamfer on the left ear (visual only)
ear_top_y = 16.0
ear_chamfer = 5.0


def _wire_outline():
    # Outer profile, CCW, U-slot as a semicircle into the body.
    pts = [
        App.Vector(tab_x0, H, 0),
        App.Vector(x_right, H, 0),
        App.Vector(x_right, shoulder_y, 0),
        App.Vector(x_notch, shoulder_y, 0),
        App.Vector(x_notch, 0, 0),
        App.Vector(x_u1, 0, 0),
    ]
    edges = [Part.makeLine(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    # U: semicircle from right opening to left opening, bulge +Y
    u_r = Part.Arc(
        App.Vector(x_u1, 0, 0),
        App.Vector(x_uc, U_W / 2.0, 0),
        App.Vector(x_u0, 0, 0),
    ).toShape()
    edges.append(u_r)
    rest = [
        App.Vector(x_u0, 0, 0),
        App.Vector(ear_chamfer, 0, 0),
        App.Vector(0, ear_chamfer, 0),
        App.Vector(0, ear_top_y, 0),
        App.Vector(tab_x0, shoulder_y, 0),
        App.Vector(tab_x0, H, 0),
    ]
    for i in range(len(rest) - 1):
        edges.append(Part.makeLine(rest[i], rest[i + 1]))
    return Part.Wire(edges)


def _boss_wire():
    # 4.5 mm island: central body including the U, not the 2 mm tab or ear tip.
    pts = [
        App.Vector(tab_x0, shoulder_y, 0),
        App.Vector(x_notch, shoulder_y, 0),
        App.Vector(x_notch, 0, 0),
        App.Vector(x_u1, 0, 0),
    ]
    edges = [Part.makeLine(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    edges.append(
        Part.Arc(
            App.Vector(x_u1, 0, 0),
            App.Vector(x_uc, U_W / 2.0, 0),
            App.Vector(x_u0, 0, 0),
        ).toShape()
    )
    rest = [
        App.Vector(x_u0, 0, 0),
        App.Vector(tab_x0, 0, 0),
        App.Vector(tab_x0, shoulder_y, 0),
    ]
    for i in range(len(rest) - 1):
        edges.append(Part.makeLine(rest[i], rest[i + 1]))
    return Part.Wire(edges)


def _hole(x, y, dia, height):
    c = Part.makeCylinder(dia / 2.0, height + 2.0, App.Vector(x, y, -1), App.Vector(0, 0, 1))
    return c


def build():
    doc = App.ActiveDocument or App.newDocument("Sprite_Befestigungsplatte")
    face = Part.Face(_wire_outline())
    plate = face.extrude(App.Vector(0, 0, T_PLATE))
    boss = Part.Face(_boss_wire()).extrude(App.Vector(0, 0, T_BOSS))
    solid = plate.fuse(boss)

    h = T_BOSS + 4.0
    for x, y, d in (
        (x_th_l, tab_y_holes, TAB_HOLE),
        (x_th_r, tab_y_holes, TAB_HOLE),
        (x_l_hole, EAR_HOLE_Y, EAR_HOLE),
        (x_r_hole, EAR_HOLE_Y, EAR_HOLE),
    ):
        solid = solid.cut(_hole(x, y, d, h))

    solid = solid.removeSplitter()
    obj = doc.addObject("Part::Feature", "Sprite_Befestigungsplatte")
    obj.Shape = solid
    obj.Label = "Sprite_Befestigungsplatte"
    doc.recompute()
    print(
        "built Sprite plate  bbox=%.2f x %.2f x %.2f  volume=%.1f"
        % (
            obj.Shape.BoundBox.XLength,
            obj.Shape.BoundBox.YLength,
            obj.Shape.BoundBox.ZLength,
            obj.Shape.Volume,
        )
    )
    print(
        "holes Ø3.20 at x=%.2f,%.2f y=%.2f ; Ø5.10 at x=%.2f,%.2f y=%.2f"
        % (x_th_l, x_th_r, tab_y_holes, x_l_hole, x_r_hole, EAR_HOLE_Y)
    )
    return obj


if __name__ == "__main__" or True:
    build()
