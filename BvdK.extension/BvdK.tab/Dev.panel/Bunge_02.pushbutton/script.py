# -*- coding: utf-8 -*-
__title__ = "Bunge Sheet Gen"
__doc__ = """Version = 1.1  (safe-mode)
Date    = 21.07.2025
________________________________________________________________
Description:
Creates a cropped prefab plan from a user-drawn boundary, numbers
Pipes/Fittings 'Comments', auto-tags pipes (no leaders), and places
the view on a new sheet (safe defaults to avoid regen thrash).
________________________________________________________________
How-To:
1. Draw a closed loop with Detail Lines around the region.
2. Run the tool, edit/confirm codes, press OK.
3. Pick a title block, done.
________________________________________________________________
Author: Emin Avdovic (safe-mode refactor)
"""

# =========================
# SAFE MODE SWITCHES
# =========================
SAFE_CFG = {
    "duplicate_with_detailing": False,  # False => minimal duplication (safer)
    "place_text_note": True,  # place tiny text note inside crop
    "autotag_pipes": True,  # create tags for untagged pipes
    "tag_use_leader": True,
    "tag_offset_mm": 250,
    "create_3d_view": False,  # disabled for stability
    "place_schedule": False,  # disabled for stability
    "hide_crop_region_controls": True,  # keep the crop visible OFF
    "annotation_crop_on": False,  # annotation crop OFF for stability
    "view_scale": 50,  # plan scale
    "view_discipline": "Coordination",  # Coordination is stable
    "write_comments_codes": False,  # keep tags independent from prefab text for now
    "use_active_view_on_sheet": True,
    "create_duplicate_plan_view": False,
    "exclude_nlrs_pipes": True,
    "tag_type_map": {
        "ACO_PIPE": ("RSen_GA_tag pipe_ACO", u"RED TypeMark + DN + \u00f8"),
        "VERT_ARROW": ("BvdK_Verdiepingspijl", "Van boven"),
        "SYS_AFM_CL": (
            "BvdK_Pipe_Riolering_Afm + spot elev_bb_H",
            "sys + afm. + hoogte cl",
        ),
    },
}

# ==================================================
# Imports
# ==================================================
from Autodesk.Revit.DB import *
from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    ElementId,
    FamilySymbol,
    FamilyInstance,
    FilteredElementCollector,
    XYZ,
    Transaction,
    TextNote,
    TextNoteType,
    TextNoteOptions,
    IndependentTag,
    UnitTypeId,
    Reference,
    TagMode,
    TagOrientation,
    ViewSheet,
    ViewDuplicateOption,
    ViewDiscipline,
    Viewport,
    ViewPlan,
    StorageType,
    Category,
)
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.UI import *
from Autodesk.Revit.DB.Structure import *
from Autodesk.Revit.Exceptions import *
from Autodesk.Revit.Attributes import *
from Autodesk.Revit.Exceptions import ArgumentException
from System.Collections.Generic import List

import clr
import System
import System.IO

clr.AddReference("System")
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
clr.AddReference("RevitServices")
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("WindowsBase")
from RevitServices.Persistence import DocumentManager
from System.Windows.Forms import (
    Form,
    ListBox,
    DataGridView,
    DataGridViewTextBoxColumn,
    DataGridViewButtonColumn,
    DataGridViewAutoSizeColumnsMode,
    DataGridViewSelectionMode,
    DockStyle,
    TextBox,
    Button,
    MessageBox,
    DialogResult,
    Label,
    Application,
    FlowLayoutPanel,
    FlowDirection,
    MessageBoxButtons,
    MessageBoxIcon,
)
from System.Drawing import Point, Color, Rectangle, Size
from System import Array
import math, re, sys

# ==================================================
# Revit Document Setup
# ==================================================
app = __revit__.Application
uidoc = __revit__.ActiveUIDocument
doc = __revit__.ActiveUIDocument.Document

VERBOSE = False
_PIPE_TAG_LOOKUP_DIAG_SHOWN = False
_MISSING_TAG_TYPES_DIALOG_SHOWN = False
_RESOLVED_TAG_SYMBOLS = {"PIPE_DEFAULT": None, "PIPE_VERTICAL": None}


def debug(*args):
    if VERBOSE:
        print(" ".join([str(a) for a in args]))


# ==================================================
# Utilities
# ==================================================
def safe_int(value, default=0):
    try:
        return int(str(value))
    except:
        return default


def mm_to_ft(mm):
    return UnitUtils.ConvertToInternalUnits(mm, UnitTypeId.Millimeters)


Z_BAND = mm_to_ft(1500.0)
XY_PAD = mm_to_ft(50.0)
INSET = mm_to_ft(15.0)  # tiny inset to avoid grazing zeros
MIN_STOREY_HEIGHT = mm_to_ft(500.0)

PIPE_TAG_DEFAULT = (
    "RSen_GA_tag pipe_ACO",
    u"RED TypeMark + DN + \u00f8",
    BuiltInCategory.OST_PipeTags,
)
PIPE_TAG_VERTICAL = (
    "BvdK_Verdiepingspijl",
    "Van boven",
    BuiltInCategory.OST_PipeTags,
)
PIPE_TAG_SPOT_ELEV = (
    "BvdK_Pipe_Riolering_Afm + spot elev_bb_H",
    "sys + afm. + hoogte cl",
    BuiltInCategory.OST_PipeTags,
)
TAG_CFG = SAFE_CFG["tag_type_map"]
TAGCFG_ACO = dict(
    family="RSen_GA_tag pipe_ACO",
    type=u"RED TypeMark + DN + \u2300",
    leader=True,
    offset_mm=400,
)
TAGCFG_VERD = dict(
    family="BvdK_Verdiepingspijl",
    type="Van boven",
    leader=False,
    offset_mm=0,
)


def points_are_close(pt1, pt2, tol=1e-6):
    return (
        abs(pt1.X - pt2.X) < tol
        and abs(pt1.Y - pt2.Y) < tol
        and abs(pt1.Z - pt2.Z) < tol
    )


def order_segments_to_polygon(segments):
    if not segments:
        return None
    polygon = [segments[0][0], segments[0][1]]
    segments.pop(0)
    changed = True
    while segments and changed:
        changed = False
        last_pt = polygon[-1]
        for idx, seg in enumerate(segments):
            ptA, ptB = seg
            if points_are_close(last_pt, ptA):
                polygon.append(ptB)
                segments.pop(idx)
                changed = True
                break
            elif points_are_close(last_pt, ptB):
                polygon.append(ptA)
                segments.pop(idx)
                changed = True
                break
    if polygon and points_are_close(polygon[0], polygon[-1]):
        polygon.pop()
        return polygon
    return None


def is_point_inside_polygon(point, polygon):
    x, y = point.X, point.Y
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i].X, polygon[i].Y
        xj, yj = polygon[j].X, polygon[j].Y
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        ):
            inside = not inside
        j = i
    return inside


def polygon_bounds_xy(poly):
    minx = min(p.X for p in poly)
    maxx = max(p.X for p in poly)
    miny = min(p.Y for p in poly)
    maxy = max(p.Y for p in poly)
    spanx = maxx - minx
    spany = maxy - miny

    inset_x = min(INSET, spanx * 0.1, spanx * 0.49) if spanx > 0 else 0.0
    inset_y = min(INSET, spany * 0.1, spany * 0.49) if spany > 0 else 0.0

    pminx = minx + inset_x
    pmaxx = maxx - inset_x
    pminy = miny + inset_y
    pmaxy = maxy - inset_y

    if pminx >= pmaxx:
        pminx, pmaxx = minx, maxx
    if pminy >= pmaxy:
        pminy, pmaxy = miny, maxy

    return (pminx, pminy, pmaxx, pmaxy)


def get_scan_levels(active_plan):
    if not isinstance(active_plan, ViewPlan):
        return None, None
    levels = list(FilteredElementCollector(doc).OfClass(Level).ToElements())
    levels = sorted(levels, key=lambda l: l.Elevation)
    if not levels:
        return None, None

    base = active_plan.GenLevel
    if base is None:
        return None, None
    next_above = None
    for lv in levels:
        delta = lv.Elevation - base.Elevation
        if delta < MIN_STOREY_HEIGHT:
            continue
        if delta > 1e-6:
            next_above = lv
            break
    return base, next_above


def _norm(s):
    if s is None:
        return ""
    s = unicode(s) if "unicode" in globals() else str(s)
    s = s.strip().lower()
    s = (
        s.replace(u"\u00d8", u"o")
        .replace(u"\u00f8", u"o")
        .replace(u"\u2300", u"o")
        .replace(u"\u03c6", u"o")
        .replace("Ã˜", u"o")
        .replace("Ã¸", u"o")
    )
    for ch in [u"_", u"-", u"\u2013", u"\u2014", u"+"]:
        s = s.replace(ch, u" ")
    s = u" ".join(s.split())
    return s


def find_family_symbol_by_family_and_type(doc, family_name, type_name):
    fam_lc = (family_name or "").strip().lower()
    typ_lc = (type_name or "").strip().lower()
    for sym in FilteredElementCollector(doc).OfClass(FamilySymbol):
        try:
            if not sym.Category or sym.Category.Id.IntegerValue != int(BuiltInCategory.OST_PipeTags):
                continue
            fam = sym.Family.Name if sym.Family else ""
            p = sym.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
            tname = p.AsString() if p else (sym.Name or "")
            if _norm(fam) == _norm(fam_lc) and _norm(tname) == _norm(typ_lc):
                return sym
        except:
            pass
    return None


def find_tag_symbol_id(doc, family_name, type_name):
    fam_lc = (family_name or "").strip().lower()
    typ_lc = (type_name or "").strip().lower()
    for sym in FilteredElementCollector(doc).OfClass(FamilySymbol):
        try:
            if not sym.Category:
                continue
            if sym.Category.Id.IntegerValue != int(BuiltInCategory.OST_PipeTags):
                continue
            fam = sym.Family.Name if sym.Family else ""
            typ = sym.Name or ""
            if _norm(fam) == _norm(fam_lc) and _norm(typ) == _norm(typ_lc):
                return sym.Id
        except Exception:
            continue
    return None


def find_tag_symbol(doc, family_name, type_name, bic=BuiltInCategory.OST_PipeTags):
    fam_tgt = _norm(family_name)
    typ_tgt = _norm(type_name)
    syms = (
        FilteredElementCollector(doc)
        .OfClass(FamilySymbol)
        .OfCategory(bic)
        .ToElements()
    )

    for s in syms:
        try:
            fam = _norm(s.Family.Name if s.Family else getattr(s, "FamilyName", ""))
            p = s.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
            typ = _norm(p.AsString() if p else s.Name)
            if fam == fam_tgt and typ == typ_tgt:
                return s
        except Exception:
            pass

    for s in syms:
        try:
            fam = _norm(s.Family.Name if s.Family else getattr(s, "FamilyName", ""))
            p = s.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
            typ = _norm(p.AsString() if p else s.Name)
            if fam == fam_tgt and (typ_tgt in typ or typ in typ_tgt):
                return s
        except Exception:
            pass

    return None


def list_pipe_tag_symbols(doc):
    lines = []
    pipe_tag_cat = Category.GetCategory(doc, BuiltInCategory.OST_PipeTags)
    if pipe_tag_cat is None:
        msg = "Pipe tag category not available in this document."
        print(msg)
        TaskDialog.Show("Pipe Tag Symbols", msg)
        return []

    syms = FilteredElementCollector(doc).OfClass(FamilySymbol).ToElements()
    for sym in syms:
        try:
            if not sym.Category or sym.Category.Id.IntegerValue != pipe_tag_cat.Id.IntegerValue:
                continue
            fam_name = ""
            if hasattr(sym, "Family") and sym.Family:
                fam_name = sym.Family.Name or ""
            if not fam_name and hasattr(sym, "FamilyName"):
                fam_name = sym.FamilyName or ""
            type_name = sym.Name or ""
            sid = sym.Id.IntegerValue
            is_active = getattr(sym, "IsActive", False)
            lines.append(
                "{} | {} | id={} | active={}".format(
                    fam_name, type_name, sid, is_active
                )
            )
        except Exception:
            continue

    lines = sorted(lines)
    for ln in lines:
        print(ln)

    if lines:
        preview = lines[:80]
        msg = "Pipe tag symbols (showing {} of {}):\n{}".format(
            len(preview), len(lines), "\n".join(preview)
        )
    else:
        msg = "No pipe tag symbols found."
    TaskDialog.Show("Pipe Tag Symbols", msg[:3800])
    return lines


def ensure_symbol_active(doc, sym_or_id):
    if sym_or_id is None:
        return False
    if hasattr(sym_or_id, "Id"):
        sym = sym_or_id
    else:
        if sym_or_id == ElementId.InvalidElementId:
            return False
        sym = doc.GetElement(sym_or_id)
    if sym is None:
        return False
    if not hasattr(sym, "IsActive") or sym.IsActive:
        return True
    if doc.IsModifiable:
        sym.Activate()
        doc.Regenerate()
        return True
    with Transaction(doc, "Activate tag symbol") as t:
        t.Start()
        sym.Activate()
        doc.Regenerate()
        t.Commit()
    return True


def get_pipe_tag_type_from_existing_tags(doc, view):
    tags = (
        FilteredElementCollector(doc, view.Id)
        .OfCategory(BuiltInCategory.OST_PipeTags)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    if tags and len(tags) > 0:
        try:
            tid = tags[0].GetTypeId()
            ensure_symbol_active(doc, tid)
            return tid
        except Exception:
            return None
    return None


def get_first_pipe_tag_symbol_id(doc):
    pipe_tag_cat = Category.GetCategory(doc, BuiltInCategory.OST_PipeTags)
    if pipe_tag_cat is None:
        return None
    syms = FilteredElementCollector(doc).OfClass(FamilySymbol).ToElements()
    for sym in syms:
        try:
            if sym.Category and sym.Category.Id.IntegerValue == pipe_tag_cat.Id.IntegerValue:
                ensure_symbol_active(doc, sym.Id)
                return sym.Id
        except Exception:
            continue
    return None


def resolve_required_tag_symbols(doc):
    global _PIPE_TAG_LOOKUP_DIAG_SHOWN
    global _MISSING_TAG_TYPES_DIALOG_SHOWN
    global _RESOLVED_TAG_SYMBOLS

    resolved = {}
    missing = []
    required = {
        "ACO_PIPE": (TAGCFG_ACO["family"], TAGCFG_ACO["type"]),
        "VERT_ARROW": (TAGCFG_VERD["family"], TAGCFG_VERD["type"]),
    }
    for key, (fam, typ) in required.items():
        sym_id = find_tag_symbol_id(doc, fam, typ)
        sym = doc.GetElement(sym_id) if sym_id else None
        if sym is None:
            sym = find_family_symbol_by_family_and_type(doc, fam, typ)
        if sym is None:
            sym = find_tag_symbol(doc, fam, typ)
        if sym:
            ensure_symbol_active(doc, sym)
            resolved[key] = sym
        else:
            missing.append(u"{} - {}".format(fam, typ))

    _RESOLVED_TAG_SYMBOLS["PIPE_DEFAULT"] = resolved.get("ACO_PIPE")
    _RESOLVED_TAG_SYMBOLS["PIPE_VERTICAL"] = resolved.get("VERT_ARROW")

    if missing:
        if not _PIPE_TAG_LOOKUP_DIAG_SHOWN:
            _PIPE_TAG_LOOKUP_DIAG_SHOWN = True
            list_pipe_tag_symbols(doc)
        if not _MISSING_TAG_TYPES_DIALOG_SHOWN:
            _MISSING_TAG_TYPES_DIALOG_SHOWN = True
            TaskDialog.Show(
                "Missing Pipe Tag Types",
                "Could not find required tag types:\n- " + "\n- ".join(missing),
            )

    return resolved, missing


def get_pipe_direction(elem):
    try:
        curve = elem.Location.Curve
        d = (curve.GetEndPoint(1) - curve.GetEndPoint(0)).Normalize()
        return XYZ(d.X, d.Y, d.Z)
    except Exception:
        return XYZ(0, 0, 0)


def is_vertical_pipe(elem):
    d = get_pipe_direction(elem)
    return abs(d.Z) > 0.85


def is_horizontal_connected_to_vertical(pipe):
    if is_vertical_pipe(pipe):
        return False
    try:
        cm = pipe.ConnectorManager if hasattr(pipe, "ConnectorManager") else None
        if not cm:
            return False
        for c in cm.Connectors:
            for r in c.AllRefs:
                owner = r.Owner
                if owner and owner.Id.IntegerValue != pipe.Id.IntegerValue:
                    if owner.Category and owner.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeCurves):
                        if is_vertical_pipe(owner):
                            return True
    except Exception:
        pass
    return False


def decide_pipe_tag_symbol_id(pipe, view):
    resolved, _ = resolve_required_tag_symbols(doc)
    pipe_tag_sym = resolved.get("ACO_PIPE")
    vert_tag_sym = resolved.get("VERT_ARROW")
    if is_vertical_pipe(pipe):
        if vert_tag_sym:
            ensure_symbol_active(doc, vert_tag_sym.Id)
            return vert_tag_sym.Id
    else:
        if pipe_tag_sym:
            ensure_symbol_active(doc, pipe_tag_sym.Id)
            return pipe_tag_sym.Id

    return None


def find_pipe_tags_for_host_in_view(doc, view_id, host_id):
    hits = []
    tags = (
        FilteredElementCollector(doc, view_id)
        .OfCategory(BuiltInCategory.OST_PipeTags)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for t in tags:
        try:
            if hasattr(t, "GetTaggedElementIds"):
                ids = t.GetTaggedElementIds()
                for rid in ids:
                    hid = rid.HostElementId if hasattr(rid, "HostElementId") else rid
                    if hid and hid.IntegerValue == host_id.IntegerValue:
                        hits.append(t)
                        break
            elif hasattr(t, "TaggedElementId") and t.TaggedElementId:
                if t.TaggedElementId.IntegerValue == host_id.IntegerValue:
                    hits.append(t)
        except Exception:
            pass
    return hits


def find_pipe_tag_in_view(view, host_id):
    tags = find_pipe_tags_for_host_in_view(doc, view.Id, host_id)
    return tags[0] if tags else None


class DetailLineSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        return bool(
            elem.Category
            and elem.Category.Id.IntegerValue == int(BuiltInCategory.OST_Lines)
        )

    def AllowReference(self, ref, point):
        return False


def convert_param_to_string(param_obj):
    if not param_obj:
        return ""
    try:
        s = param_obj.AsValueString()
        if s and s.strip():
            return s
    except:
        pass
    try:
        val_mm = param_obj.AsDouble() * 304.8
        return str(int(round(val_mm))) + " mm"
    except:
        return ""


def is_nlrs_element(elem):
    try:
        t = doc.GetElement(elem.GetTypeId())
        tn = (t.Name or "").lower() if t else ""
        fn = ""
        if isinstance(elem, FamilyInstance) and elem.Symbol and elem.Symbol.Family:
            fn = (elem.Symbol.Family.Name or "").lower()
        en = (elem.Name or "").lower()
        blob = " ".join([tn, fn, en])
        return ("nlrs_" in blob) or blob.startswith("nlrs")
    except Exception:
        return False


def is_nested_familyinstance(elem):
    try:
        return isinstance(elem, FamilyInstance) and elem.SuperComponent is not None
    except Exception:
        return False


def select_boundary_and_gather():
    if not isinstance(uidoc.ActiveView, ViewPlan):
        TaskDialog.Show(
            "Bunge Sheet Generator",
            "Run this tool from a Floor Plan/Callout (ViewPlan) for reliable prefab scanning.",
        )
        return None, None

    try:
        selection_refs = uidoc.Selection.PickObjects(
            ObjectType.Element,
            DetailLineSelectionFilter(),
            "Select boundary detail lines (closed loop)",
        )
    except Exception:
        return None, None
    if not selection_refs:
        return None, None

    segments = []
    for r in selection_refs:
        el = doc.GetElement(r)
        try:
            crv = el.GeometryCurve
            segments.append((crv.GetEndPoint(0), crv.GetEndPoint(1)))
        except:
            pass

    polygon = order_segments_to_polygon(segments[:])
    if polygon is None:
        MessageBox.Show("The selected lines do not form a closed loop.", "Error")
        return None, None

    active_plan = uidoc.ActiveView
    ground, first = get_scan_levels(active_plan)
    if ground is None:
        TaskDialog.Show("Bunge Sheet Generator", "Could not determine base level from active plan.")
        return None, None
    z_min = ground.Elevation - mm_to_ft(500.0)
    z_max = (
        first.Elevation + mm_to_ft(500.0)
        if first is not None
        else (ground.Elevation + mm_to_ft(4000.0) + mm_to_ft(500.0))
    )
    if abs(z_max - z_min) < 1e-6:
        z_max = z_min + Z_BAND

    pminx, pminy, pmaxx, pmaxy = polygon_bounds_xy(polygon)
    dz_mm = (z_max - z_min) * 304.8
    print(
        "Scan bounds XY=({:.4f}, {:.4f})-({:.4f}, {:.4f}) Z=({:.4f}, {:.4f}) dz_mm={:.1f}".format(
            pminx, pminy, pmaxx, pmaxy, z_min, z_max, dz_mm
        )
    )
    allowed_cat_ids = {
        int(BuiltInCategory.OST_PipeCurves),
        int(BuiltInCategory.OST_PipeFitting),
        int(BuiltInCategory.OST_PipeAccessory),
        int(BuiltInCategory.OST_PlumbingFixtures),
    }
    collector = (
        FilteredElementCollector(doc, uidoc.ActiveView.Id)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    elements_inside = []

    for elem in collector:
        if not elem.Category:
            continue
        if elem.Category.Id.IntegerValue not in allowed_cat_ids:
            continue
        if SAFE_CFG.get("exclude_nlrs_pipes", True) and is_nlrs_element(elem):
            continue
        if (
            elem.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeFitting)
            and is_nested_familyinstance(elem)
        ):
            continue
        bb = elem.get_BoundingBox(uidoc.ActiveView)
        if not bb:
            continue
        center = XYZ(
            (bb.Min.X + bb.Max.X) * 0.5,
            (bb.Min.Y + bb.Max.Y) * 0.5,
            (bb.Min.Z + bb.Max.Z) * 0.5,
        )
        if not (z_min <= center.Z <= z_max):
            continue
        if is_point_inside_polygon(XYZ(center.X, center.Y, 0), polygon):
            elements_inside.append(elem)

    MessageBox.Show(
        "Found {0} element(s) in region.".format(len(elements_inside)), "Boundary"
    )
    return elements_inside, polygon


# ==================================================
# Data grid UI (unchanged core behavior; cleaned a bit)
# ==================================================
from System.Windows.Forms import (
    Panel,
    DataGridViewTextBoxColumn,
    DataGridViewButtonColumn,
    DataGridViewAutoSizeColumnsMode,
)


class ElementEditorForm(Form):
    def __init__(self, elements_data, region_elements=None):
        self.Text = "Edit Element Codes"
        self.Width = 1050
        self.Height = 500
        self.MinimumSize = Size(700, 400)
        self.SuspendLayout()
        self.regionElements = region_elements
        self.regionPolygon = None

        self.buttonPanel = Panel()
        self.buttonPanel.Height = 48
        self.buttonPanel.Dock = DockStyle.Bottom
        self.Controls.Add(self.buttonPanel)
        self.rightPanel = Panel()
        self.rightPanel.Dock = DockStyle.Right
        self.rightPanel.Width = 320
        self.buttonPanel.Controls.Add(self.rightPanel)
        self.leftPanel = Panel()
        self.leftPanel.Dock = DockStyle.Fill
        self.buttonPanel.Controls.Add(self.leftPanel)
        self.leftFlow = FlowLayoutPanel()
        self.leftFlow.Dock = DockStyle.Fill
        self.leftFlow.WrapContents = False
        self.leftFlow.AutoSize = False
        self.leftPanel.Controls.Add(self.leftFlow)
        self.rightFlow = FlowLayoutPanel()
        self.rightFlow.Dock = DockStyle.Fill
        self.rightFlow.WrapContents = False
        self.rightFlow.FlowDirection = FlowDirection.RightToLeft
        self.rightFlow.AutoSize = False
        self.rightPanel.Controls.Add(self.rightFlow)
        self.gridPanel = Panel()
        self.gridPanel.Dock = DockStyle.Fill
        self.Controls.Add(self.gridPanel)

        self.dataGrid = DataGridView()
        self.dataGrid.Dock = DockStyle.Fill
        self.dataGrid.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill
        self.dataGrid.MultiSelect = True
        self.dataGrid.SelectionMode = DataGridViewSelectionMode.FullRowSelect
        self.dataGrid.SelectionChanged += self.on_row_selected
        self.gridPanel.Controls.Add(self.dataGrid)

        # columns
        cols = []

        def add_col(name, header, ro=True):
            c = DataGridViewTextBoxColumn()
            c.Name = name
            c.HeaderText = header
            c.ReadOnly = ro
            cols.append(c)

        add_col("Id", "Element Id")
        add_col("Category", "Category")
        add_col("Name", "Name")
        add_col("Warning", "Warning")
        add_col("Bend45", u"2x45\u00b0")
        add_col("DefaultCode", "Default Code")
        add_col("NewCode", "New Code", ro=False)
        add_col("OutsideDiameter", "Outside Diameter")
        add_col("Length", "Length")
        add_col("Size", "Size")
        add_col("GEB_Article_Number", "GEB Article No.")

        self.colTagStatus = DataGridViewButtonColumn()
        self.colTagStatus.Name = "TagStatus"
        self.colTagStatus.HeaderText = "Tags"
        self.colTagStatus.UseColumnTextForButtonValue = False

        self.dataGrid.Columns.AddRange(Array[DataGridViewTextBoxColumn](cols))
        self.dataGrid.Columns.Add(self.colTagStatus)
        self.dataGrid.CellContentClick += self.dataGrid_CellContentClick

        # text note input
        self.txtTextNoteCode = TextBox()
        self.txtTextNoteCode.Width = 160
        self.txtTextNoteCode.ForeColor = Color.Gray
        self.txtTextNoteCode.Text = "prefab 5.5.5"
        self.txtTextNoteCode.GotFocus += self._clear_placeholder
        self.txtTextNoteCode.LostFocus += self._restore_placeholder
        self.leftFlow.Controls.Add(self.txtTextNoteCode)

        # buttons
        def add_btn(text, handler, w=150):
            b = Button()
            b.Text = text
            b.Width = w
            b.Click += handler
            self.leftFlow.Controls.Add(b)
            return b

        self.btnPlacePrefabNote = add_btn(
            "Place Prefab Text Note", self.placePrefabTextNote, 180
        )
        self.btnAutoFill = add_btn("Auto-Fill Tag Codes", self.autoFillPipeTagCodes)
        self.btnBulkTags = add_btn("Bulk Add/Remove Tags", self.bulkAddRemoveTags, 180)
        self.btnOK = add_btn("OK", self.okButton_Click, 80)
        self.btnCancel = add_btn("Cancel", self._cancel, 80)

        self.lblPipes = Label()
        self.lblPipes.Width = 160
        self.rightFlow.Controls.Add(self.lblPipes)
        self.lblFittings = Label()
        self.lblFittings.Width = 130
        self.rightFlow.Controls.Add(self.lblFittings)
        self.lblAccessories = Label()
        self.lblAccessories.Width = 130
        self.rightFlow.Controls.Add(self.lblAccessories)

        self.textNotePlaced = False
        self.Result = None

        # populate
        for ed in elements_data:
            ridx = self.dataGrid.Rows.Add()
            row = self.dataGrid.Rows[ridx]
            row.Cells["Id"].Value = ed["Id"]
            row.Cells["Category"].Value = ed["Category"]
            row.Cells["Name"].Value = ed["Name"]
            row.Cells["Warning"].Value = ed.get("Warning", "")
            row.Cells["Bend45"].Value = ed.get("Bend45", "")
            row.Cells["DefaultCode"].Value = ed["DefaultCode"]
            row.Cells["NewCode"].Value = ed["NewCode"]
            row.Cells["OutsideDiameter"].Value = ed["OutsideDiameter"]
            row.Cells["Length"].Value = ed["Length"]
            row.Cells["Size"].Value = ed.get("Size", "")
            row.Cells["GEB_Article_Number"].Value = ed.get("GEB_Article_Number", "")

            cat = ed["Category"]
            if cat == "Pipes":
                row.Cells["TagStatus"].Value = (
                    "Remove Tag" if ed["TagStatus"] == "Yes" else "Add/Place Tag"
                )
                row.DefaultCellStyle.BackColor = Color.LightBlue
            elif cat == "Pipe Fittings":
                row.Cells["TagStatus"].Value = ""
                row.Cells["TagStatus"].ReadOnly = True
                row.DefaultCellStyle.BackColor = Color.LightGoldenrodYellow
            elif cat == "Pipe Accessories":
                row.Cells["TagStatus"].Value = ""
                row.Cells["TagStatus"].ReadOnly = True
                row.DefaultCellStyle.BackColor = Color.LightYellow
            else:
                row.Cells["TagStatus"].Value = ""
                row.DefaultCellStyle.BackColor = Color.LightGray

        self.update_counters()
        self.ResumeLayout(False)

    def _layout_buttons(self, sender, e):
        pass

    def _clear_placeholder(self, sender, e):
        if self.txtTextNoteCode.Text == "prefab 5.5.5":
            self.txtTextNoteCode.Text = ""
            self.txtTextNoteCode.ForeColor = Color.Black

    def _restore_placeholder(self, sender, e):
        if not self.txtTextNoteCode.Text.strip():
            self.txtTextNoteCode.Text = "prefab 5.5.5"
            self.txtTextNoteCode.ForeColor = Color.Gray

    def update_counters(self):
        pipes_total = 0
        pipes_tagged = 0
        fittings_total = 0
        accessories_total = 0
        for i in range(self.dataGrid.Rows.Count):
            row = self.dataGrid.Rows[i]
            cat = row.Cells["Category"].Value or ""
            if cat == "Pipes":
                pipes_total += 1
                if row.Cells["TagStatus"].Value == "Remove Tag":
                    pipes_tagged += 1
            elif cat == "Pipe Fittings":
                fittings_total += 1
            elif cat == "Pipe Accessories":
                accessories_total += 1
        self.lblPipes.Text = "Pipes: {}/{}".format(pipes_tagged, pipes_total)
        self.lblFittings.Text = "Fittings: {}".format(fittings_total)
        self.lblAccessories.Text = "Accessories: {}".format(accessories_total)

    def find_pipe_tags_in_view_for_host(self, view, host_elem_id):
        return find_pipe_tags_for_host_in_view(doc, view.Id, host_elem_id)

    def _create_pipe_tag_for_host(self, host):
        if not host:
            return (False, None)
        bb = host.get_BoundingBox(uidoc.ActiveView)
        if not bb:
            return (False, None)
        ctr = XYZ(
            (bb.Min.X + bb.Max.X) * 0.5,
            (bb.Min.Y + bb.Max.Y) * 0.5,
            (bb.Min.Z + bb.Max.Z) * 0.5,
        )
        symbol_id = decide_pipe_tag_symbol_id(host, uidoc.ActiveView)
        if symbol_id is None:
            if is_vertical_pipe(host):
                fam, typ = TAG_CFG["VERT_ARROW"]
            else:
                fam, typ = TAG_CFG["ACO_PIPE"]
            return (False, "{} - {}".format(fam, typ))
        tag = IndependentTag.Create(
            doc,
            doc.ActiveView.Id,
            Reference(host),
            SAFE_CFG["tag_use_leader"],
            TagMode.TM_ADDBY_CATEGORY,
            TagOrientation.Horizontal,
            ctr,
        )
        try:
            tag.ChangeTypeId(symbol_id)
        except Exception as ex:
            doc.Delete(tag.Id)
            TaskDialog.Show(
                "Tag Type Error",
                "Failed to apply tag type to created tag.\nHost: {}\nError: {}".format(
                    host.Id.IntegerValue, ex
                ),
            )
            return (False, None)
        return (True, None)

    def refresh_pipe_tag_rows_and_status(self):
        region_pipe_ids = set()
        if self.regionElements:
            for e in self.regionElements:
                try:
                    if (
                        e
                        and e.Category
                        and e.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeCurves)
                    ):
                        region_pipe_ids.add(e.Id.IntegerValue)
                except Exception:
                    pass

        for i in range(self.dataGrid.Rows.Count):
            r = self.dataGrid.Rows[i]
            if (r.Cells["Category"].Value or "") != "Pipes":
                continue
            hid = safe_int(r.Cells["Id"].Value)
            host = doc.GetElement(ElementId(hid))
            has_tag = bool(
                host
                and (
                    not region_pipe_ids
                    or host.Id.IntegerValue in region_pipe_ids
                )
                and find_pipe_tag_in_view(uidoc.ActiveView, host.Id)
            )
            r.Cells["TagStatus"].Value = "Remove Tag" if has_tag else "Add/Place Tag"

        self.update_counters()

    def placePrefabTextNote(self, sender, e):
        raw = self.txtTextNoteCode.Text.strip()
        if not raw:
            MessageBox.Show("Please enter a prefab text first.", "Info")
            return
        if not self.regionPolygon:
            MessageBox.Show("No region polygon available.", "Error")
            return
        pminx, pminy, _, _ = polygon_bounds_xy(self.regionPolygon)
        note_pt = XYZ(pminx + XY_PAD, pminy + XY_PAD, 0)
        prefab_re = re.compile(r"\d+(?:\.\d+)+")
        notes = (
            FilteredElementCollector(doc, uidoc.ActiveView.Id)
            .OfClass(TextNote)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        target = None
        for n in notes:
            try:
                txt = n.Text or ""
                p = n.Coord
                if prefab_re.search(txt) and is_point_inside_polygon(
                    XYZ(p.X, p.Y, 0), self.regionPolygon
                ):
                    target = n
                    break
            except Exception:
                continue

        with Transaction(doc, "Place Prefab Text Note") as t:
            t.Start()
            if target:
                target.Text = raw
            else:
                nt = FilteredElementCollector(doc).OfClass(TextNoteType).FirstElement()
                if nt:
                    TextNote.Create(
                        doc, uidoc.ActiveView.Id, note_pt, raw, TextNoteOptions(nt.Id)
                    )
            t.Commit()
        self.textNotePlaced = True

    def bulkAddRemoveTags(self, sender, e):
        choice = MessageBox.Show(
            "Yes: Remove all pipe tags for all pipes in region.\nNo: Add missing pipe tags for all pipes in region.",
            "Bulk Add/Remove Tags",
            MessageBoxButtons.YesNoCancel,
            MessageBoxIcon.Question,
        )
        if choice == DialogResult.Cancel:
            return

        pipe_rows = [
            self.dataGrid.Rows[i]
            for i in range(self.dataGrid.Rows.Count)
            if (self.dataGrid.Rows[i].Cells["Category"].Value or "") == "Pipes"
        ]
        if not pipe_rows:
            return

        pipes = []
        for row in pipe_rows:
            host_id = safe_int(row.Cells["Id"].Value)
            host = doc.GetElement(ElementId(host_id))
            if host:
                pipes.append(host)

        missing_types = set()
        failures = []
        action = "add_missing" if choice == DialogResult.No else "remove_all"
        missing_types, failures = tag_pipes_in_view(
            uidoc.ActiveView, pipes, action=action, source_view=uidoc.ActiveView
        )
        self.refresh_pipe_tag_rows_and_status()
        if missing_types:
            TaskDialog.Show(
                "Missing Pipe Tag Types",
                "Could not find required tag types:\n- {}".format(
                    "\n- ".join(sorted(missing_types))
                ),
            )
        if failures:
            TaskDialog.Show(
                "Tag Failures",
                "Tag creation failed for pipe ids:\n- {}".format(
                    "\n- ".join([str(x) for x in sorted(set(failures))])
                ),
            )

    def autoFillPipeTagCodes(self, sender, e):
        raw = self.txtTextNoteCode.Text.strip()
        m = re.search(r"([\d\.]+)", raw)
        if not m:
            MessageBox.Show("Could not parse base code from text note.", "Error")
            return
        base = m.group(1)

        pipe_rows, fit_rows = [], []
        for i in range(self.dataGrid.Rows.Count):
            cat = self.dataGrid.Rows[i].Cells["Category"].Value
            if cat == "Pipes":
                pipe_rows.append(i)
            elif cat == "Pipe Fittings":
                fit_rows.append(i)

        # fittings = base
        for i in fit_rows:
            self.dataGrid.Rows[i].Cells["NewCode"].Value = base

        # pipes numbered by X,Y
        entries = []
        for i in pipe_rows:
            rid = safe_int(self.dataGrid.Rows[i].Cells["Id"].Value)
            elem = doc.GetElement(ElementId(rid))
            bb = elem.get_BoundingBox(uidoc.ActiveView) if elem else None
            ctr = (
                XYZ(
                    (bb.Min.X + bb.Max.X) * 0.5,
                    (bb.Min.Y + bb.Max.Y) * 0.5,
                    (bb.Min.Z + bb.Max.Z) * 0.5,
                )
                if bb
                else XYZ(0, 0, 0)
            )
            entries.append((i, ctr))
        entries.sort(key=lambda x: (x[1].X, x[1].Y))
        for n, (i, _) in enumerate(entries, 1):
            self.dataGrid.Rows[i].Cells["NewCode"].Value = "{}.{}".format(base, n)

        self.update_counters()

    def dataGrid_CellContentClick(self, sender, e):
        # Only handle basic Pipe Tag add/remove here (leaderless); no fitting flips in safe-mode
        if self.dataGrid.Columns[e.ColumnIndex].Name != "TagStatus":
            return
        row = self.dataGrid.Rows[e.RowIndex]
        cat = row.Cells["Category"].Value
        val = row.Cells["TagStatus"].Value
        if cat != "Pipes":
            return

        host_id = safe_int(row.Cells["Id"].Value)
        host = doc.GetElement(ElementId(host_id))
        if not host:
            return

        if val == "Add/Place Tag":
            missing_types, failures = tag_pipes_in_view(
                uidoc.ActiveView, [host], action="add_missing", source_view=uidoc.ActiveView
            )
            self.refresh_pipe_tag_rows_and_status()
            if missing_types:
                TaskDialog.Show(
                    "Missing Pipe Tag Type",
                    "Could not find required tag type:\n- {}".format(
                        "\n- ".join(sorted(missing_types))
                    ),
                )
            elif failures:
                TaskDialog.Show(
                    "Tag Creation Failed",
                    "Could not create a pipe tag for host id {}".format(
                        host.Id.IntegerValue
                    ),
                )
        elif val == "Remove Tag":
            _, _ = tag_pipes_in_view(
                uidoc.ActiveView, [host], action="remove_all", source_view=uidoc.ActiveView
            )
            self.refresh_pipe_tag_rows_and_status()

    def okButton_Click(self, sender, e):
        updated = []
        for r in self.dataGrid.Rows:
            updated.append(
                {
                    "Id": r.Cells["Id"].Value,
                    "Category": r.Cells["Category"].Value,
                    "Name": r.Cells["Name"].Value,
                    "DefaultCode": r.Cells["DefaultCode"].Value,
                    "NewCode": r.Cells["NewCode"].Value,
                    "TagStatus": r.Cells["TagStatus"].Value,
                }
            )
        self.Result = {
            "Elements": updated,
            "TextNotePlaced": self.textNotePlaced,
            "TextNote": self.txtTextNoteCode.Text.strip(),
        }
        self.DialogResult = DialogResult.OK
        self.Close()

    def _cancel(self, sender, e):
        self.Result = None
        self.DialogResult = DialogResult.Cancel
        self.Close()

    def on_row_selected(self, sender, e):
        row = self.dataGrid.CurrentRow
        if not row:
            return
        id_val = row.Cells["Id"].Value
        if not id_val:
            return
        try:
            eid = ElementId(int(str(id_val)))
            elem = doc.GetElement(eid)
            if elem and elem.IsValidObject:
                uidoc.Selection.SetElementIds(List[ElementId]([eid]))
        except:
            return


def show_element_editor(elements_data, region_elements=None, region_polygon=None):
    f = ElementEditorForm(elements_data, region_elements)
    f.regionPolygon = region_polygon
    return f.ShowDialog() == DialogResult.OK and f.Result or None


def choose_post_editor_workflow():
    td = TaskDialog("Bunge Sheet Generator")
    td.MainInstruction = "Choose what to do next"
    td.MainContent = "Apply changes only, or continue with plan sheet creation."
    td.AddCommandLink(
        TaskDialogCommandLinkId.CommandLink1,
        "Apply changes only (no sheet)",
    )
    td.AddCommandLink(
        TaskDialogCommandLinkId.CommandLink2,
        "Apply + create sheet (plan only for now)",
    )
    td.CommonButtons = TaskDialogCommonButtons.Cancel
    r = td.Show()
    if r == TaskDialogResult.CommandLink2:
        return "sheet"
    if r == TaskDialogResult.CommandLink1:
        return "apply_only"
    return "cancel"


# ==================================================
# Filtering for relevant elements
# ==================================================
def filter_relevant_elements(gathered_elements):
    relevant = []
    source_view = uidoc.ActiveView

    for e in gathered_elements:
        if not e.Category:
            continue
        cat = e.Category.Name
        if cat not in (
            "Pipes",
            "Pipe Fittings",
            "Pipe Accessories",
            "Text Notes",
        ):
            continue

        com = e.LookupParameter("Comments")
        default_code = com.AsString() if com and com.AsString() else ""

        warning_val = ""
        bend45_val = ""
        outside_diam = ""
        length_val = ""
        art_num = ""
        tag_status = ""

        if cat == "Pipes":
            odp = e.LookupParameter("Outside Diameter")
            lp = e.LookupParameter("Length")
            outside_diam = convert_param_to_string(odp)
            length_val = convert_param_to_string(lp)

            tag_status = "Yes" if find_pipe_tag_in_view(source_view, e.Id) else "No"

        elif cat == "Pipe Fittings":
            p_warn = e.LookupParameter("waarschuwing")
            warning_val = p_warn.AsString() if p_warn else ""
            p_bend = e.LookupParameter(u"2x45\u00b0")
            if p_bend and p_bend.StorageType == StorageType.Integer:
                bend45_val = "Yes" if p_bend.AsInteger() == 1 else "No"
            for pname in ("Outside Diameter", "Diameter", "Nominal Diameter"):
                p = e.LookupParameter(pname)
                if p:
                    outside_diam = convert_param_to_string(p)
                    break
            lp = e.LookupParameter("Length")
            length_val = convert_param_to_string(lp)
            ap = e.LookupParameter("GEB_Article_Number")
            art_num = ap.AsString() if (ap and ap.AsString()) else ""
            tag_status = ""  # fittings not tagged here in safe-mode

        elif cat == "Pipe Accessories":
            tag_status = ""  # accessories not tagged in this workflow

        relevant.append(
            {
                "Id": str(e.Id),
                "Category": cat,
                "Name": e.Name if hasattr(e, "Name") else "",
                "Warning": warning_val,
                "Bend45": bend45_val,
                "DefaultCode": default_code,
                "NewCode": default_code,
                "OutsideDiameter": outside_diam,
                "Length": length_val,
                "Size": "",
                "GEB_Article_Number": art_num,
                "TagStatus": tag_status,
            }
        )
    return relevant


# ==================================================
# Tagging helpers (leaderless, single transaction)
# ==================================================
def _midpoint_or_center(elem, view):
    try:
        loc = getattr(elem, "Location", None)
        if loc and hasattr(loc, "Curve") and loc.Curve:
            return loc.Curve.Evaluate(0.5, True)
    except:
        pass
    bb = elem.get_BoundingBox(view)
    if not bb:
        return XYZ(0, 0, 0)
    return XYZ(
        (bb.Min.X + bb.Max.X) / 2.0,
        (bb.Min.Y + bb.Max.Y) / 2.0,
        (bb.Min.Z + bb.Max.Z) / 2.0,
    )


def view_offset_vector(view, mm):
    d = mm_to_ft(mm)
    try:
        right = view.RightDirection
        up = view.UpDirection
        return XYZ(
            right.X * d + up.X * (0.25 * d),
            right.Y * d + up.Y * (0.25 * d),
            right.Z * d + up.Z * (0.25 * d),
        )
    except Exception:
        return XYZ(d, d * 0.25, 0)


def tag_pipes_in_view(view, pipes, action="add_missing", source_view=None):
    """
    action:
      - add_missing: add tags where missing
      - remove_all: remove all tags
      - sync_from_source: if source has tag -> ensure present in view, else remove
    """
    if source_view is None:
        source_view = view
    resolved, _ = resolve_required_tag_symbols(doc)
    sym_aco = resolved.get("ACO_PIPE")
    sym_vert = resolved.get("VERT_ARROW")
    missing_types = set()
    failures = []
    with Transaction(doc, "Tag pipes in view") as t:
        t.Start()
        for p in pipes:
            if not p or not p.Category or p.Category.Id.IntegerValue != int(BuiltInCategory.OST_PipeCurves):
                continue
            existing = find_pipe_tags_for_host_in_view(doc, view.Id, p.Id)
            source_has = bool(find_pipe_tag_in_view(source_view, p.Id))

            do_add = False
            do_remove = False
            if action == "remove_all":
                do_remove = True
            elif action == "add_missing":
                do_add = True
            elif action == "sync_from_source":
                do_add = source_has
                do_remove = not source_has

            if do_remove and existing:
                for tdel in existing:
                    doc.Delete(tdel.Id)
                existing = []
            if do_add and existing:
                continue
            if not do_add:
                continue

            is_vert = is_vertical_pipe(p)
            if is_vert:
                symbol_id = sym_vert.Id if sym_vert else None
                use_leader = bool(TAGCFG_VERD.get("leader", False))
                use_offset_mm = TAGCFG_VERD.get("offset_mm", 0)
            else:
                symbol_id = sym_aco.Id if sym_aco else None
                use_leader = bool(TAGCFG_ACO.get("leader", True))
                use_offset_mm = TAGCFG_ACO.get("offset_mm", SAFE_CFG.get("tag_offset_mm", 250))
            if symbol_id is None:
                if is_vertical_pipe(p):
                    fam, typ = TAG_CFG["VERT_ARROW"]
                else:
                    fam, typ = TAG_CFG["ACO_PIPE"]
                missing_types.add("{} - {}".format(fam, typ))
                continue

            try:
                pt = _midpoint_or_center(p, view)
                tag = IndependentTag.Create(
                    doc,
                    view.Id,
                    Reference(p),
                    use_leader,
                    TagMode.TM_ADDBY_CATEGORY,
                    TagOrientation.Horizontal,
                    pt,
                )
                tag.ChangeTypeId(symbol_id)
                if use_leader:
                    try:
                        tag.HasLeader = True
                    except Exception:
                        pass
                if use_offset_mm and use_offset_mm > 0:
                    off = view_offset_vector(view, use_offset_mm)
                    try:
                        tag.TagHeadPosition = XYZ(
                            pt.X + off.X, pt.Y + off.Y, pt.Z + off.Z
                        )
                    except Exception:
                        pass
            except Exception:
                failures.append(p.Id.IntegerValue)
        doc.Regenerate()
        t.Commit()
    return missing_types, failures


def get_untagged_pipes_in_view(doc, view):
    pipes = list(
        FilteredElementCollector(doc, view.Id)
        .OfCategory(BuiltInCategory.OST_PipeCurves)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    tagged = set()
    for t in (
        FilteredElementCollector(doc, view.Id)
        .OfCategory(BuiltInCategory.OST_PipeTags)
        .WhereElementIsNotElementType()
        .ToElements()
    ):
        try:
            if hasattr(t, "GetTaggedElementIds"):
                for rid in t.GetTaggedElementIds():
                    hid = rid.HostElementId if hasattr(rid, "HostElementId") else rid
                    tagged.add(hid.IntegerValue)
            elif hasattr(t, "TaggedElementId") and t.TaggedElementId:
                tagged.add(t.TaggedElementId.IntegerValue)
        except:
            pass
    return [p for p in pipes if p.Id.IntegerValue not in tagged]


def auto_tag_pipes_in_view(doc, view, use_leader=False, tag_aco=None, tag_vert=None):
    untagged = get_untagged_pipes_in_view(doc, view)
    if not untagged:
        return 0
    if tag_aco is not None:
        _RESOLVED_TAG_SYMBOLS["PIPE_DEFAULT"] = tag_aco
    if tag_vert is not None:
        _RESOLVED_TAG_SYMBOLS["PIPE_VERTICAL"] = tag_vert
    missing, failures = tag_pipes_in_view(
        view, untagged, action="add_missing", source_view=view
    )
    if missing:
        TaskDialog.Show(
            "Missing Pipe Tag Types",
            "Could not find required tag types:\n- {}".format("\n- ".join(sorted(missing))),
        )
    return max(0, len(untagged) - len(failures))


def ensure_prefab_text_note_in_view(view, polygon, text_value):
    if not text_value:
        return
    pminx, pminy, _, _ = polygon_bounds_xy(polygon)
    note_pt = XYZ(pminx + XY_PAD, pminy + XY_PAD, 0)
    prefab_re = re.compile(r"\d+(?:\.\d+)+")
    notes = (
        FilteredElementCollector(doc, view.Id)
        .OfClass(TextNote)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    target = None
    for n in notes:
        try:
            txt = n.Text or ""
            p = n.Coord
            if prefab_re.search(txt) and is_point_inside_polygon(
                XYZ(p.X, p.Y, 0), polygon
            ):
                target = n
                break
        except Exception:
            continue

    with Transaction(doc, "Ensure Prefab Text Note") as t:
        t.Start()
        if target:
            target.Text = text_value
        else:
            nt = FilteredElementCollector(doc).OfClass(TextNoteType).FirstElement()
            if nt:
                TextNote.Create(doc, view.Id, note_pt, text_value, TextNoteOptions(nt.Id))
        t.Commit()


def get_view_id_for_sheet(doc, source_view, desired_name=None):
    try:
        if getattr(source_view, "IsTemplate", False):
            return None, "Source view is a template"
    except Exception:
        pass

    def _duplicate_with_detailing():
        new_id = source_view.Duplicate(ViewDuplicateOption.WithDetailing)
        new_view = doc.GetElement(new_id)
        if desired_name:
            try:
                new_view.Name = desired_name
            except Exception:
                try:
                    new_view.Name = "{} ({})".format(
                        desired_name, new_view.Id.IntegerValue
                    )
                except Exception:
                    pass
        return new_id

    try:
        if doc.IsModifiable:
            return _duplicate_with_detailing(), "DuplicatedWithDetailing"
        with Transaction(doc, "Create sheet-placeable duplicate view") as t:
            t.Start()
            nid = _duplicate_with_detailing()
            t.Commit()
        return nid, "DuplicatedWithDetailing"
    except Exception as ex:
        return None, "DuplicateFailed: {}".format(ex)


def apply_tags_and_text_in_active_view(view, gathered_elements, result, polygon):
    pipes_by_id = {}
    for e in gathered_elements:
        if (
            e
            and e.Category
            and e.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeCurves)
        ):
            pipes_by_id[e.Id.IntegerValue] = e

    to_add = []
    to_remove = []
    for e_data in result.get("Elements", []):
        if e_data.get("Category") != "Pipes":
            continue
        eid = safe_int(e_data.get("Id"), 0)
        if eid <= 0 or eid not in pipes_by_id:
            continue
        status = (e_data.get("TagStatus") or "").strip()
        if status == "Remove Tag":
            to_add.append(pipes_by_id[eid])
        elif status == "Add/Place Tag":
            to_remove.append(pipes_by_id[eid])

    if to_remove:
        tag_pipes_in_view(view, to_remove, action="remove_all", source_view=view)
    missing_types, failures = tag_pipes_in_view(
        view, to_add, action="add_missing", source_view=view
    )
    if missing_types:
        TaskDialog.Show(
            "Missing Pipe Tag Types",
            "Could not find required tag types:\n- {}".format(
                "\n- ".join(sorted(missing_types))
            ),
        )
    if failures:
        TaskDialog.Show(
            "Tag Failures",
            "Tag creation failed for pipe ids:\n- {}".format(
                "\n- ".join([str(x) for x in sorted(set(failures))])
            ),
        )

    ensure_prefab_text_note_in_view(
        view, polygon, (result.get("TextNote", "") or "").strip()
    )


# ==================================================
# MAIN
# ==================================================
gathered_elements, polygon = select_boundary_and_gather()
if not gathered_elements:
    MessageBox.Show("No elements were gathered. Cancelled.", "Error")
    sys.exit("Cancelled")

filtered = filter_relevant_elements(gathered_elements)
if not filtered:
    MessageBox.Show("No relevant elements found in region.", "Error")
    sys.exit("Cancelled")

result = show_element_editor(
    filtered, region_elements=gathered_elements, region_polygon=polygon
)
if result is None:
    sys.exit("Cancelled")

workflow_choice = choose_post_editor_workflow()
if workflow_choice == "cancel":
    sys.exit("Cancelled")

uidoc.Selection.SetElementIds(List[ElementId]())

# Force fittings to base
baseCode = result["TextNote"]
for eData in result["Elements"]:
    if eData["Category"] == "Pipe Fittings":
        eData["NewCode"] = baseCode

# Renumber pipes if no placed text note
if not result.get("TextNotePlaced", False):
    base_raw = (result.get("TextNote", "") or "").strip()
    m = re.search(r"([\d\.]+)", base_raw)
    base = m.group(1) if m else "0"
    pipe_entries = []
    for idx, eData in enumerate(result["Elements"]):
        if eData["Category"] == "Pipes":
            elem = doc.GetElement(ElementId(int(str(eData["Id"]))))
            if elem:
                bb = elem.get_BoundingBox(uidoc.ActiveView)
                if bb:
                    center = XYZ(
                        (bb.Min.X + bb.Max.X) / 2.0,
                        (bb.Min.Y + bb.Max.Y) / 2.0,
                        (bb.Min.Z + bb.Max.Z) / 2.0,
                    )
                    pipe_entries.append((idx, center))
    pipe_entries.sort(key=lambda x: (x[1].X, x[1].Y))
    ctr = 1
    for i, _ in pipe_entries:
        result["Elements"][i]["NewCode"] = base + "." + str(ctr)
        ctr += 1
    for eData in result["Elements"]:
        if eData["Category"] == "Pipe Fittings":
            eData["NewCode"] = base

source_view = uidoc.ActiveView
if not isinstance(source_view, ViewPlan):
    MessageBox.Show("Active view is not a plan/callout view.", "Error")
    sys.exit()

# -------- Phase 1: apply tags + text note (always committed) --------
resolved_syms, missing_req = resolve_required_tag_symbols(doc)
if missing_req:
    TaskDialog.Show(
        "Missing Pipe Tag Types",
        "Could not find required tag types:\n- " + "\n- ".join(missing_req),
    )

apply_tags_and_text_in_active_view(source_view, gathered_elements, result, polygon)

# Update Comments (optional in safe-mode)
if SAFE_CFG.get("write_comments_codes", False):
    with Transaction(doc, "Update Comments (safe)") as t:
        t.Start()
        for eData in result["Elements"]:
            id_val = eData.get("Id")
            try:
                eid = int(str(id_val))
            except:
                continue
            elem = doc.GetElement(ElementId(eid))
            if not elem:
                continue
            p = elem.LookupParameter("Comments")
            if not p or p.IsReadOnly:
                continue
            if eData["Category"] == "Pipes":
                continue
            if eData["Category"] == "Pipe Fittings":
                p.Set(result["TextNote"])
            else:
                p.Set(str(eData["NewCode"]))
        t.Commit()

if workflow_choice == "apply_only":
    TaskDialog.Show("Bunge Sheet Gen", "Tags/text placed. Sheet skipped.")
    sys.exit()

# -------- Phase 2: create sheet (isolated; failures do not rollback phase 1) --------
m = re.search(r"([\d\.]+)", result["TextNote"])
base = m.group(1) if m else (result["TextNote"] or "").strip()

# -------- Title-block picker & sheet creation (simple) --------
# collect title-blocks
all_tbs = list(
    FilteredElementCollector(doc)
    .OfCategory(BuiltInCategory.OST_TitleBlocks)
    .OfClass(FamilySymbol)
    .ToElements()
)

if not all_tbs:
    MessageBox.Show("No title-block types found.", "Error")
    sys.exit()


class TBPicker(Form):
    def __init__(self, tbs):
        self.tbs = tbs
        self.Text = "Choose a Title-Block"
        self.ClientSize = Size(340, 360)
        self.lb = ListBox()
        self.lb.Bounds = Rectangle(10, 10, 320, 300)
        for sym in tbs:
            fam = sym.Family.Name if sym.Family else ""
            type_name = (
                sym.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM).AsString() or ""
            )
            self.lb.Items.Add(fam + " - " + type_name)
        self.Controls.Add(self.lb)
        ok = Button(Text="OK", DialogResult=DialogResult.OK, Location=Point(10, 320))
        ca = Button(
            Text="Cancel", DialogResult=DialogResult.Cancel, Location=Point(100, 320)
        )
        self.Controls.Add(ok)
        self.Controls.Add(ca)
        self.AcceptButton = ok
        self.CancelButton = ca


existing_sheets = FilteredElementCollector(doc).OfClass(ViewSheet).ToElements()
existing_numbers = {s.SheetNumber for s in existing_sheets}

picker = TBPicker(all_tbs)
if picker.ShowDialog() != DialogResult.OK or picker.lb.SelectedIndex < 0:
    MessageBox.Show("Sheet creation cancelled. Tags/text are kept.", "Info")
    sys.exit()

title_block = all_tbs[picker.lb.SelectedIndex]
sheet_code = base
if sheet_code in existing_numbers:
    sheet_code = "{} ({})".format(base, source_view.Id.IntegerValue)

# Ensure TB active
try:
    if hasattr(title_block, "IsActive") and (not title_block.IsActive):
        with Transaction(doc, "Activate title block") as t_act:
            t_act.Start()
            title_block.Activate()
            t_act.Commit()
except:
    pass

try:
    with Transaction(doc, "Create sheet & place plan (safe)") as t3:
        t3.Start()
        sheet = ViewSheet.Create(doc, title_block.Id)
        sheet.SheetNumber = sheet_code
        sheet.Name = "Prefab " + sheet_code

        # center-ish placement
        view_id_to_place = source_view.Id
        if not Viewport.CanAddViewToSheet(doc, sheet.Id, view_id_to_place):
            dup_name = "{} - Sheet".format(sheet_code)
            view_id_to_place, _why = get_view_id_for_sheet(
                doc, source_view, desired_name=dup_name
            )
        if (
            not view_id_to_place
            or not Viewport.CanAddViewToSheet(doc, sheet.Id, view_id_to_place)
        ):
            raise Exception("Could not place view on sheet (even after duplication).")
        center = XYZ(
            (sheet.Outline.Min.U + sheet.Outline.Max.U) / 2.0,
            (sheet.Outline.Min.V + sheet.Outline.Max.V) / 2.0,
            0,
        )
        Viewport.Create(doc, sheet.Id, view_id_to_place, center)

        # schedules & 3D intentionally skipped in safe-mode
        t3.Commit()
    MessageBox.Show(
        "Prefab sheet '{}' created and plan placed.".format(sheet.SheetNumber), "Done"
    )
except Exception as ex:
    TaskDialog.Show(
        "Bunge Sheet Gen",
        "Sheet creation failed, but tags/text are kept.\n\n{}".format(ex),
    )

