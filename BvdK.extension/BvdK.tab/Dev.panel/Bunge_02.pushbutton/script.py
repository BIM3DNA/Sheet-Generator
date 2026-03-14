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
        "ACO_PIPE": ("RSen_GA_tag pipe_ACO", "RED TypeMark + DN + \u00f8"),
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
_RESOLVED_TAG_SYMBOLS = {
    "PIPE_DEFAULT": None,
    "PIPE_VERTICAL": None,
    "PIPE_EXTRA_HORIZONTAL": None,
}
_EXPLODED_VIEW_VECTORS = {}


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
    "RED TypeMark + DN + \u00f8",
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
    type="RED TypeMark + DN + \u2300",
    leader=True,
    offset_mm=500,
)
TAGCFG_VERD = dict(
    family="BvdK_Verdiepingspijl",
    type="Van boven",
    leader=False,
    offset_mm=0,
)
TAGCFG_EXTRA_H = dict(
    family="BvdK_Pipe_Riolering_Afm + spot elev_bb_L RED",
    type="RED sys + afm. + hoogte cl",
    leader=True,
    offset_mm=350,
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


def normalize_tag_text(s):
    if s is None:
        return ""
    s = unicode(s) if "unicode" in globals() else str(s)
    s = s.strip().lower()
    s = (
        s.replace("\u00d8", "ø")
        .replace("\u00f8", "ø")
        .replace("\u2300", "ø")
        .replace("\u03c6", "ø")
        .replace("Ã˜", "ø")
        .replace("Ã¸", "ø")
        .replace("diameter", "ø")
    )
    for ch in [
        "_",
        "-",
        "\u2013",
        "\u2014",
        "+",
        ".",
        ",",
        ":",
        ";",
        "(",
        ")",
        "[",
        "]",
    ]:
        s = s.replace(ch, " ")
    s = " ".join(s.split())
    return s


def _norm(s):
    return normalize_tag_text(s)


def get_symbol_type_name(sym):
    try:
        p = sym.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if p:
            sval = p.AsString()
            if sval:
                return sval
    except Exception:
        pass
    try:
        return sym.Name or ""
    except Exception:
        return ""


def discover_pipe_tag_symbol_candidates(doc, family_hints=None):
    candidates = []
    hints = [_norm(x) for x in (family_hints or [])]
    for sym in FilteredElementCollector(doc).OfClass(FamilySymbol):
        try:
            fam = sym.Family.Name if sym.Family else getattr(sym, "FamilyName", "")
            typ = get_symbol_type_name(sym)
            cat_name = sym.Category.Name if sym.Category else ""
            fam_n = _norm(fam)
            typ_n = _norm(typ)
            cat_n = _norm(cat_name)
            is_pipe_tag_like = (
                "pipe tag" in cat_n
                or "tag pipe" in cat_n
                or "tag" in cat_n
                or "tag" in fam_n
                or any(h in fam_n for h in hints)
            )
            if is_pipe_tag_like:
                candidates.append(
                    {
                        "symbol": sym,
                        "family": fam,
                        "type": typ,
                        "family_n": fam_n,
                        "type_n": typ_n,
                        "category": cat_name,
                    }
                )
        except Exception:
            continue
    return candidates


def find_family_symbol_by_family_and_type(doc, family_name, type_name):
    fam_lc = _norm(family_name)
    typ_lc = _norm(type_name)
    for item in discover_pipe_tag_symbol_candidates(doc, [family_name]):
        if item["family_n"] == fam_lc and item["type_n"] == typ_lc:
            return item["symbol"]
    return None


def choose_best_symbol_for_family(doc, family_name, preferred_types):
    fam_lc = _norm(family_name)
    family_candidates = [
        item
        for item in discover_pipe_tag_symbol_candidates(doc, [family_name])
        if item["family_n"] == fam_lc
    ]
    if family_candidates:
        print("TAG RESOLVE -> family candidates for", family_name)
        for item in family_candidates:
            print(
                "  candidate:",
                item["family"],
                "|",
                item["type"],
                "| normalized={}".format(item["type_n"]),
                "| category={}".format(item["category"]),
                "| id={}".format(item["symbol"].Id.IntegerValue),
            )
    for pref in preferred_types:
        pref_n = _norm(pref)
        for item in family_candidates:
            if item["type_n"] == pref_n:
                print(
                    "TAG RESOLVE -> exact normalized match:",
                    item["family"],
                    "|",
                    item["type"],
                    "| id={}".format(item["symbol"].Id.IntegerValue),
                )
                return item["symbol"]
    for pref in preferred_types:
        pref_n = _norm(pref)
        for item in family_candidates:
            if (
                item["type_n"].startswith(pref_n)
                or pref_n.startswith(item["type_n"])
                or pref_n in item["type_n"]
                or item["type_n"] in pref_n
            ):
                print(
                    "TAG RESOLVE -> fallback normalized match:",
                    item["family"],
                    "|",
                    item["type"],
                    "| id={}".format(item["symbol"].Id.IntegerValue),
                )
                return item["symbol"]
    return family_candidates[0]["symbol"] if family_candidates else None


def find_tag_symbol_id(doc, family_name, type_name):
    fam_lc = _norm(family_name)
    typ_lc = _norm(type_name)
    family_candidates = []
    for item in discover_pipe_tag_symbol_candidates(doc, [family_name]):
        try:
            sym = item["symbol"]
            fam = item["family"]
            typ = item["type"]
            fam_n = item["family_n"]
            typ_n = item["type_n"]
            if fam_n == fam_lc:
                family_candidates.append((sym, fam, typ, typ_n, item["category"]))
            if fam_n == fam_lc and typ_n == typ_lc:
                print(
                    "TAG RESOLVE -> exact match:",
                    fam,
                    "|",
                    typ,
                    "| category={}".format(item["category"]),
                    "| id={}".format(sym.Id.IntegerValue),
                )
                return sym.Id
        except Exception:
            continue
    for sym, fam, typ, typ_n, cat_name in family_candidates:
        if typ_n.startswith(typ_lc) or typ_lc.startswith(typ_n) or typ_lc in typ_n:
            print(
                "TAG RESOLVE -> fallback match:",
                fam,
                "|",
                typ,
                "| category={}".format(cat_name),
                "| id={}".format(sym.Id.IntegerValue),
            )
            return sym.Id
    if family_candidates:
        print("TAG RESOLVE -> family candidates for", family_name)
        for sym, fam, typ, _, cat_name in family_candidates:
            print(
                "  candidate:",
                fam,
                "|",
                typ,
                "| category={}".format(cat_name),
                "| id={}".format(sym.Id.IntegerValue),
            )
    return None


def find_tag_symbol(doc, family_name, type_name, bic=BuiltInCategory.OST_PipeTags):
    fam_tgt = _norm(family_name)
    typ_tgt = _norm(type_name)
    syms = (
        FilteredElementCollector(doc).OfClass(FamilySymbol).OfCategory(bic).ToElements()
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
    for item in discover_pipe_tag_symbol_candidates(
        doc, [TAGCFG_ACO["family"], TAGCFG_VERD["family"]]
    ):
        sym = item["symbol"]
        sid = sym.Id.IntegerValue
        is_active = getattr(sym, "IsActive", False)
        lines.append(
            "{} | {} | category={} | id={} | active={}".format(
                item["family"], item["type"], item["category"], sid, is_active
            )
        )

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
            if (
                sym.Category
                and sym.Category.Id.IntegerValue == pipe_tag_cat.Id.IntegerValue
            ):
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
        "ACO_PIPE": (
            TAGCFG_ACO["family"],
            [
                TAGCFG_ACO["type"],
                "TypeMark + DN + ø",
                "DN + Length",
            ],
        ),
        "VERT_ARROW": (TAGCFG_VERD["family"], [TAGCFG_VERD["type"]]),
        "EXTRA_HORIZONTAL": (TAGCFG_EXTRA_H["family"], [TAGCFG_EXTRA_H["type"]]),
    }
    for key, (fam, preferred_types) in required.items():
        sym = choose_best_symbol_for_family(doc, fam, preferred_types)
        if sym is None:
            sym_id = find_tag_symbol_id(doc, fam, preferred_types[0])
            sym = doc.GetElement(sym_id) if sym_id else None
        if sym is None:
            sym = find_family_symbol_by_family_and_type(doc, fam, preferred_types[0])
        if sym is None:
            sym = find_tag_symbol(doc, fam, preferred_types[0])
        if sym:
            ensure_symbol_active(doc, sym)
            resolved[key] = sym
            try:
                print(
                    "TAG RESOLVE -> resolved:",
                    sym.Family.Name if sym.Family else "",
                    "|",
                    sym.Name,
                    "| id={}".format(sym.Id.IntegerValue),
                )
            except Exception:
                pass
        else:
            missing.append("{} - {}".format(fam, preferred_types[0]))

    _RESOLVED_TAG_SYMBOLS["PIPE_DEFAULT"] = resolved.get("ACO_PIPE")
    _RESOLVED_TAG_SYMBOLS["PIPE_VERTICAL"] = resolved.get("VERT_ARROW")
    _RESOLVED_TAG_SYMBOLS["PIPE_EXTRA_HORIZONTAL"] = resolved.get("EXTRA_HORIZONTAL")
    print(
        "TAG RESOLVE -> extra horizontal tag:",
        (
            resolved.get("EXTRA_HORIZONTAL").Id.IntegerValue
            if resolved.get("EXTRA_HORIZONTAL")
            else None
        ),
    )

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
    try:
        crv = elem.Location.Curve
        d = crv.Direction.Normalize()
        return abs(d.Z) > 0.9
    except Exception:
        d = get_pipe_direction(elem)
        return abs(d.Z) > 0.9


def is_horizontal_pipe(elem):
    try:
        crv = elem.Location.Curve
        d = crv.Direction.Normalize()
        return abs(d.Z) < 0.15
    except Exception:
        d = get_pipe_direction(elem)
        return abs(d.Z) < 0.15


def get_elem_type_name(elem):
    try:
        tid = elem.GetTypeId()
        if tid and tid.IntegerValue > 0:
            t = doc.GetElement(tid)
            if t:
                try:
                    return Element.Name.GetValue(t) or ""
                except Exception:
                    try:
                        return t.Name or ""
                    except Exception:
                        return ""
    except Exception:
        pass
    return ""


def get_elem_family_name(elem):
    try:
        tid = elem.GetTypeId()
        if tid and tid.IntegerValue > 0:
            t = doc.GetElement(tid)
            if t:
                try:
                    return t.FamilyName or ""
                except Exception:
                    try:
                        if hasattr(t, "Family") and t.Family:
                            return t.Family.Name or ""
                    except Exception:
                        pass
    except Exception:
        pass
    return ""


def normalize_token(s):
    try:
        return (s or "").strip().lower()
    except Exception:
        return ""


def is_excluded_nlrs_pipe(elem):
    try:
        if not elem or not elem.Category:
            return False
        if elem.Category.Id.IntegerValue != int(BuiltInCategory.OST_PipeCurves):
            return False
        type_name = normalize_token(get_elem_type_name(elem))
        fam_name = normalize_token(get_elem_family_name(elem))
        if type_name == normalize_token("NLRS_50_PI_Mapress Koper buis (OD)_geb"):
            return True
        if "nlrs_50_pi_mapress koper buis" in type_name:
            return True
        if "nlrs_50_pi_mapress koper buis" in fam_name:
            return True
        return False
    except Exception:
        return False


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
                    if owner.Category and owner.Category.Id.IntegerValue == int(
                        BuiltInCategory.OST_PipeCurves
                    ):
                        if is_vertical_pipe(owner):
                            return True
    except Exception:
        pass
    return False


def get_pipe_connectors(pipe):
    try:
        cm = pipe.ConnectorManager
    except Exception:
        try:
            cm = pipe.MEPModel.ConnectorManager
        except Exception:
            cm = None
    if not cm:
        return []
    try:
        return [c for c in cm.Connectors]
    except Exception:
        return []


def get_connected_pipe_ids(pipe, scanned_pipe_id_set):
    out = set()
    for c in get_pipe_connectors(pipe):
        try:
            for ref in c.AllRefs:
                owner = ref.Owner
                if not owner:
                    continue
                if owner.Id.IntegerValue == pipe.Id.IntegerValue:
                    continue
                if owner.Id.IntegerValue in scanned_pipe_id_set:
                    if owner.Category and owner.Category.Id.IntegerValue == int(
                        BuiltInCategory.OST_PipeCurves
                    ):
                        out.add(owner.Id.IntegerValue)
        except Exception:
            pass
    return out


def find_extra_horizontal_targets(scanned_pipes):
    scanned_pipe_id_set = set(p.Id.IntegerValue for p in scanned_pipes if p is not None)
    by_id = {p.Id.IntegerValue: p for p in scanned_pipes if p is not None}
    targets = set()
    verticals = [p for p in scanned_pipes if p is not None and is_vertical_pipe(p)]
    for vp in verticals:
        connected_ids = get_connected_pipe_ids(vp, scanned_pipe_id_set)
        horiz = []
        for cid in connected_ids:
            cp = by_id.get(cid)
            if cp and is_horizontal_pipe(cp):
                horiz.append(cp)
        horiz = sorted(horiz, key=lambda p: p.Id.IntegerValue)
        if horiz:
            targets.add(horiz[0].Id.IntegerValue)
    return targets, verticals


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
        TaskDialog.Show(
            "Bunge Sheet Generator", "Could not determine base level from active plan."
        )
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
    excluded_scan_nlrs = 0

    for elem in collector:
        if not elem.Category:
            continue
        if elem.Category.Id.IntegerValue not in allowed_cat_ids:
            continue
        if SAFE_CFG.get("exclude_nlrs_pipes", True) and (
            is_nlrs_element(elem) or is_excluded_nlrs_pipe(elem)
        ):
            if elem.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeCurves):
                excluded_scan_nlrs += 1
            continue
        if elem.Category.Id.IntegerValue == int(
            BuiltInCategory.OST_PipeFitting
        ) and is_nested_familyinstance(elem):
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

    print("SCAN -> excluded NLRS pipes:", excluded_scan_nlrs)
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
        add_col("Bend45", "2x45\u00b0")
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
                        and e.Category.Id.IntegerValue
                        == int(BuiltInCategory.OST_PipeCurves)
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
                and (not region_pipe_ids or host.Id.IntegerValue in region_pipe_ids)
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
                uidoc.ActiveView,
                [host],
                action="add_missing",
                source_view=uidoc.ActiveView,
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
                uidoc.ActiveView,
                [host],
                action="remove_all",
                source_view=uidoc.ActiveView,
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
        if cat == "Pipes" and is_excluded_nlrs_pipe(e):
            continue
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
            p_bend = e.LookupParameter("2x45\u00b0")
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
            if (
                not p
                or not p.Category
                or p.Category.Id.IntegerValue != int(BuiltInCategory.OST_PipeCurves)
            ):
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
                use_offset_mm = TAGCFG_ACO.get(
                    "offset_mm", SAFE_CFG.get("tag_offset_mm", 250)
                )
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
            "Could not find required tag types:\n- {}".format(
                "\n- ".join(sorted(missing))
            ),
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
                TextNote.Create(
                    doc, view.Id, note_pt, text_value, TextNoteOptions(nt.Id)
                )
        t.Commit()


def count_pipe_tags_in_view(doc, view):
    try:
        return (
            FilteredElementCollector(doc, view.Id)
            .OfCategory(BuiltInCategory.OST_PipeTags)
            .WhereElementIsNotElementType()
            .GetElementCount()
        )
    except Exception:
        return -1


def count_pipe_tags_for_scanned_pipes(doc, view, scanned_pipe_ids):
    return len(get_pipe_tag_ids_for_hosts_in_view(doc, view, set(scanned_pipe_ids)))


def get_existing_pipe_tagged_host_ids_in_view(doc, view):
    host_ids = set()
    tags = (
        FilteredElementCollector(doc, view.Id)
        .OfCategory(BuiltInCategory.OST_PipeTags)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for tag in tags:
        try:
            if hasattr(tag, "GetTaggedElementIds"):
                ids = tag.GetTaggedElementIds()
                for rid in ids:
                    hid = rid.HostElementId if hasattr(rid, "HostElementId") else rid
                    if hid:
                        host_ids.add(hid.IntegerValue)
            elif hasattr(tag, "TaggedElementId") and tag.TaggedElementId:
                host_ids.add(tag.TaggedElementId.IntegerValue)
        except Exception:
            continue
    return host_ids


def get_pipe_tags_for_hosts_in_view(doc, view, host_id_set):
    result = []
    tags = (
        FilteredElementCollector(doc, view.Id)
        .OfCategory(BuiltInCategory.OST_PipeTags)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for tag in tags:
        try:
            tagged_match = False
            if hasattr(tag, "GetTaggedElementIds"):
                ids = tag.GetTaggedElementIds()
                for rid in ids:
                    hid = rid.HostElementId if hasattr(rid, "HostElementId") else rid
                    if hid and hid.IntegerValue in host_id_set:
                        tagged_match = True
                        break
            elif hasattr(tag, "TaggedElementId") and tag.TaggedElementId:
                if tag.TaggedElementId.IntegerValue in host_id_set:
                    tagged_match = True
            if tagged_match:
                result.append(tag)
        except Exception:
            continue
    return result


def collect_pipe_tags_by_host(view, scanned_pipe_ids):
    by_host = {}
    tags = get_pipe_tags_for_hosts_in_view(doc, view, set(scanned_pipe_ids))
    for tag in tags:
        host_ids = []
        try:
            if hasattr(tag, "GetTaggedElementIds"):
                for rid in tag.GetTaggedElementIds():
                    hid = rid.HostElementId if hasattr(rid, "HostElementId") else rid
                    if hid and hid.IntegerValue in scanned_pipe_ids:
                        host_ids.append(hid.IntegerValue)
            elif hasattr(tag, "TaggedElementId") and tag.TaggedElementId:
                if tag.TaggedElementId.IntegerValue in scanned_pipe_ids:
                    host_ids.append(tag.TaggedElementId.IntegerValue)
        except Exception:
            host_ids = []
        for hid in host_ids:
            by_host.setdefault(hid, []).append(tag)
    return by_host


def resolve_pipe_tag_kind(pipe):
    return "VERT_ARROW" if is_vertical_pipe(pipe) else "ACO_PIPE"


def tag_matches_kind(tag, desired_kind):
    try:
        tag_type = doc.GetElement(tag.GetTypeId())
        fam = tag_type.Family.Name if tag_type and tag_type.Family else ""
        typ = get_symbol_type_name(tag_type) if tag_type else ""
    except Exception:
        return False
    fam_n = _norm(fam)
    typ_n = _norm(typ)
    if desired_kind == "VERT_ARROW":
        return fam_n == _norm(TAGCFG_VERD["family"]) and typ_n == _norm(
            TAGCFG_VERD["type"]
        )
    preferred = [
        _norm(TAGCFG_ACO["type"]),
        _norm("TypeMark + DN + ø"),
        _norm("DN + Length"),
    ]
    return fam_n == _norm(TAGCFG_ACO["family"]) and typ_n in preferred


def get_pipe_tag_ids_for_hosts_in_view(doc, view, host_id_set):
    ids = []
    tags = (
        FilteredElementCollector(doc, view.Id)
        .OfCategory(BuiltInCategory.OST_PipeTags)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for tag in tags:
        try:
            matched = False
            if hasattr(tag, "GetTaggedElementIds"):
                for rid in tag.GetTaggedElementIds():
                    hid = rid.HostElementId if hasattr(rid, "HostElementId") else rid
                    if hid and hid.IntegerValue in host_id_set:
                        matched = True
                        break
            elif hasattr(tag, "TaggedElementId") and tag.TaggedElementId:
                if tag.TaggedElementId.IntegerValue in host_id_set:
                    matched = True
            if matched:
                ids.append(tag.Id.IntegerValue)
        except Exception:
            continue
    return ids


def get_pipe_tag_for_host_in_view(doc, view, host_id):
    tags = (
        FilteredElementCollector(doc, view.Id)
        .OfCategory(BuiltInCategory.OST_PipeTags)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for tag in tags:
        try:
            if hasattr(tag, "GetTaggedElementIds"):
                for rid in tag.GetTaggedElementIds():
                    hid = rid.HostElementId if hasattr(rid, "HostElementId") else rid
                    if hid and hid.IntegerValue == host_id.IntegerValue:
                        return tag
            elif hasattr(tag, "TaggedElementId") and tag.TaggedElementId:
                if tag.TaggedElementId.IntegerValue == host_id.IntegerValue:
                    return tag
        except Exception:
            continue
    return None


def find_specific_tag_on_host_in_view(doc, view, host_id, family_name, type_name=None):
    tags = find_pipe_tags_for_host_in_view(doc, view.Id, host_id)
    fam_n = _norm(family_name)
    typ_n = _norm(type_name) if type_name else ""
    for tag in tags:
        try:
            tag_type = doc.GetElement(tag.GetTypeId())
            fam = tag_type.Family.Name if tag_type and tag_type.Family else ""
            typ = get_symbol_type_name(tag_type) if tag_type else ""
            if _norm(fam) != fam_n:
                continue
            if type_name and _norm(typ) != typ_n:
                continue
            return tag
        except Exception:
            continue
    return None


def count_specific_pipe_tags_in_view(doc, view, family_name, type_name=None):
    count = 0
    for tag in (
        FilteredElementCollector(doc, view.Id)
        .OfCategory(BuiltInCategory.OST_PipeTags)
        .WhereElementIsNotElementType()
        .ToElements()
    ):
        try:
            tag_type = doc.GetElement(tag.GetTypeId())
            fam = tag_type.Family.Name if tag_type and tag_type.Family else ""
            typ = get_symbol_type_name(tag_type) if tag_type else ""
            if _norm(fam) != _norm(family_name):
                continue
            if type_name and _norm(typ) != _norm(type_name):
                continue
            count += 1
        except Exception:
            continue
    return count


def create_sheet_safe_plan_from_scanned_view(doc, source_view, desired_name):
    last_error = None
    source_tag_count = count_pipe_tags_in_view(doc, source_view)
    source_tagged_host_ids = get_existing_pipe_tagged_host_ids_in_view(doc, source_view)
    source_extra_tag_count = count_specific_pipe_tags_in_view(
        doc, source_view, TAGCFG_EXTRA_H["family"], TAGCFG_EXTRA_H["type"]
    )
    with Transaction(doc, "Create sheet-safe plan clone") as t:
        t.Start()
        new_view = None
        for opt in [ViewDuplicateOption.WithDetailing, ViewDuplicateOption.Duplicate]:
            try:
                new_id = source_view.Duplicate(opt)
                new_view = doc.GetElement(new_id)
                break
            except Exception as ex:
                last_error = ex
                new_view = None
        if new_view is None:
            t.RollBack()
            raise Exception(
                "Failed to clone scanned source view for sheet: {}".format(last_error)
            )

        try:
            new_view.ViewTemplateId = ElementId.InvalidElementId
        except Exception:
            pass
        try:
            new_view.Scale = source_view.Scale
        except Exception:
            pass
        try:
            new_view.CropBoxActive = source_view.CropBoxActive
            new_view.CropBoxVisible = source_view.CropBoxVisible
            new_view.CropBox = source_view.CropBox
        except Exception:
            pass
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
        source_tag_ids = [
            tag.Id
            for tag in (
                FilteredElementCollector(doc, source_view.Id)
                .OfCategory(BuiltInCategory.OST_PipeTags)
                .WhereElementIsNotElementType()
                .ToElements()
            )
        ]
        source_text_note_ids = [
            note.Id
            for note in (
                FilteredElementCollector(doc, source_view.Id)
                .OfClass(TextNote)
                .WhereElementIsNotElementType()
                .ToElements()
            )
        ]
        copied_text_count = 0
        copied_text_ids = []
        clone_tag_count_pre = count_pipe_tags_in_view(doc, new_view)
        clone_scanned_tag_count_pre = count_pipe_tags_for_scanned_pipes(
            doc, new_view, source_tagged_host_ids
        )
        clone_text_count_pre = (
            FilteredElementCollector(doc, new_view.Id)
            .OfClass(TextNote)
            .WhereElementIsNotElementType()
            .GetElementCount()
        )
        print(
            "PLAN CLONE -> source floor plan tag count before clone:", source_tag_count
        )
        print(
            "PLAN CLONE -> clone floor plan tag count after clone:", clone_tag_count_pre
        )
        print(
            "PLAN CLONE -> clone scanned pipe tag count after clone:",
            clone_scanned_tag_count_pre,
        )
        if source_tag_ids and clone_tag_count_pre < len(source_tag_ids):
            try:
                ElementTransformUtils.CopyElements(
                    source_view,
                    List[ElementId](source_tag_ids),
                    new_view,
                    Transform.Identity,
                    CopyPasteOptions(),
                )
            except Exception as ex:
                print("PLAN CLONE -> tag copy fallback/skip:", ex)
        if source_text_note_ids and clone_text_count_pre < len(source_text_note_ids):
            try:
                new_ids = ElementTransformUtils.CopyElements(
                    source_view,
                    List[ElementId](source_text_note_ids),
                    new_view,
                    Transform.Identity,
                    CopyPasteOptions(),
                )
                copied_text_count = len(source_text_note_ids)
                copied_text_ids = [x.IntegerValue for x in new_ids] if new_ids else []
            except Exception as ex:
                print("PLAN CLONE -> text note copy fallback/skip:", ex)
        t.Commit()
    clone_tag_count = count_pipe_tags_in_view(doc, new_view)
    clone_scanned_tag_count = count_pipe_tags_for_scanned_pipes(
        doc, new_view, source_tagged_host_ids
    )
    clone_extra_tag_count = count_specific_pipe_tags_in_view(
        doc, new_view, TAGCFG_EXTRA_H["family"], TAGCFG_EXTRA_H["type"]
    )
    print("PLAN CLONE -> source view:", source_view.Name, source_view.Id.IntegerValue)
    print("PLAN CLONE -> clone view:", new_view.Name, new_view.Id.IntegerValue)
    print("PLAN CLONE -> pipe tags in source:", source_tag_count)
    print(
        "PLAN CLONE -> clone floor plan tag count after explicit copy pass:",
        clone_tag_count,
    )
    print(
        "PLAN CLONE -> clone scanned pipe tag count after explicit copy pass:",
        clone_scanned_tag_count,
    )
    print("PLAN CLONE -> extra horizontal tags in source:", source_extra_tag_count)
    print("PLAN CLONE -> extra horizontal tags in clone:", clone_extra_tag_count)
    print("PLAN CLONE -> prefab text note ids copied:", copied_text_ids)
    return new_view


def build_scanned_bbox(doc, elems, pad_xy_mm=400.0, pad_z_mm=400.0):
    mins = []
    maxs = []
    for elem in elems:
        if not elem:
            continue
        try:
            bb = elem.get_BoundingBox(None)
        except Exception:
            bb = None
        if not bb:
            continue
        mins.append(bb.Min)
        maxs.append(bb.Max)
    if not mins or not maxs:
        return None
    pad_xy = mm_to_ft(pad_xy_mm)
    pad_z = mm_to_ft(pad_z_mm)
    min_x = min(p.X for p in mins) - pad_xy
    min_y = min(p.Y for p in mins) - pad_xy
    min_z = min(p.Z for p in mins) - pad_z
    max_x = max(p.X for p in maxs) + pad_xy
    max_y = max(p.Y for p in maxs) + pad_xy
    max_z = max(p.Z for p in maxs) + pad_z
    bb = BoundingBoxXYZ()
    bb.Min = XYZ(min_x, min_y, min_z)
    bb.Max = XYZ(max_x, max_y, max_z)
    return bb


def get_3d_view_family_type(doc):
    for vft in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        try:
            if vft.ViewFamily == ViewFamily.ThreeDimensional:
                return vft
        except Exception:
            continue
    return None


def get_prefab_3d_elements(scanned_elems):
    filtered_scanned_pipes = []
    filtered_scanned_fittings = []
    excluded_nlrs = 0
    for elem in scanned_elems:
        if not elem or not elem.Category:
            continue
        if is_excluded_nlrs_pipe(elem):
            excluded_nlrs += 1
            continue
        cat_id = elem.Category.Id.IntegerValue
        if cat_id == int(BuiltInCategory.OST_PipeCurves):
            filtered_scanned_pipes.append(elem)
        elif cat_id == int(BuiltInCategory.OST_PipeFitting):
            filtered_scanned_fittings.append(elem)
    three_d_source_elems = filtered_scanned_pipes + filtered_scanned_fittings
    print("3D SOURCE -> pipes used:", len(filtered_scanned_pipes))
    print("3D SOURCE -> fittings used:", len(filtered_scanned_fittings))
    print("3D SOURCE -> total used:", len(three_d_source_elems))
    return (
        three_d_source_elems,
        filtered_scanned_pipes,
        filtered_scanned_fittings,
        excluded_nlrs,
    )


def hide_irrelevant_3d_content(view3d, relevant_ids):
    hidden_cats = []
    hidden_link_count = 0
    allowed_ids = set(
        [
            int(BuiltInCategory.OST_PipeCurves),
            int(BuiltInCategory.OST_PipeFitting),
            int(BuiltInCategory.OST_TextNotes),
            int(BuiltInCategory.OST_PipeTags),
        ]
    )
    for cat in doc.Settings.Categories:
        try:
            if not cat or not view3d.CanCategoryBeHidden(cat.Id):
                continue
            if cat.Id.IntegerValue in allowed_ids:
                continue
            nm = cat.Name or ""
            view3d.SetCategoryHidden(cat.Id, True)
            hidden_cats.append(nm)
        except Exception:
            continue
    try:
        for cat_id in [BuiltInCategory.OST_RvtLinks]:
            cat = Category.GetCategory(doc, cat_id)
            if cat and view3d.CanCategoryBeHidden(cat.Id):
                view3d.SetCategoryHidden(cat.Id, True)
                hidden_cats.append(cat.Name)
    except Exception:
        pass
    try:
        link_ids = List[ElementId]()
        for link_inst in (
            FilteredElementCollector(doc).OfClass(RevitLinkInstance).ToElements()
        ):
            link_ids.Add(link_inst.Id)
        if link_ids.Count > 0:
            view3d.HideElements(link_ids)
            hidden_link_count = link_ids.Count
    except Exception:
        hidden_link_count = 0
    return hidden_cats, hidden_link_count


def get_connected_prefab_components(elems):
    ids = set(e.Id.IntegerValue for e in elems if e is not None)
    by_id = {e.Id.IntegerValue: e for e in elems if e is not None}
    graph = dict((eid, set()) for eid in ids)
    for elem in elems:
        if not elem:
            continue
        eid = elem.Id.IntegerValue
        for c in get_pipe_connectors(elem):
            try:
                for ref in c.AllRefs:
                    owner = ref.Owner
                    if not owner:
                        continue
                    oid = owner.Id.IntegerValue
                    if oid == eid:
                        continue
                    if oid in ids:
                        graph[eid].add(oid)
                        graph[oid].add(eid)
            except Exception:
                pass
    seen = set()
    components = []
    for eid in sorted(graph.keys()):
        if eid in seen:
            continue
        stack = [eid]
        comp = []
        seen.add(eid)
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for nxt in sorted(graph.get(cur, [])):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        components.append(sorted(comp))
    groups = [[by_id[eid] for eid in comp if eid in by_id] for comp in components]
    print("3D EXPLODED -> connected groups:", len(components))
    print("3D EXPLODED -> group sizes:", [len(c) for c in components])
    return groups


def create_prefab_3d_normal_view(doc, base_code, scanned_elems):
    vft = get_3d_view_family_type(doc)
    if vft is None:
        raise Exception("No 3D ViewFamilyType found.")
    relevant_elems, filtered_scanned_pipes, filtered_scanned_fittings, excluded_nlrs = (
        get_prefab_3d_elements(scanned_elems)
    )
    section_box = build_scanned_bbox(doc, relevant_elems)
    if section_box is None:
        raise Exception("Could not compute scanned element extents for 3D view.")
    pipe_count = len(filtered_scanned_pipes)
    fitting_count = len(filtered_scanned_fittings)
    with Transaction(doc, "Create 3D Normal View") as t:
        t.Start()
        view3d = View3D.CreateIsometric(doc, vft.Id)
        try:
            view3d.Name = "{} - 3D Normal".format(base_code)
        except Exception:
            view3d.Name = "{} - 3D Normal ({})".format(
                base_code, view3d.Id.IntegerValue
            )
        view3d.IsSectionBoxActive = True
        view3d.SetSectionBox(section_box)
        try:
            view3d.Scale = 50
        except Exception:
            pass
        try:
            view3d.DetailLevel = ViewDetailLevel.Fine
        except Exception:
            pass
        try:
            p = view3d.get_Parameter(BuiltInParameter.VIEW_DETAIL_LEVEL)
            if p and not p.IsReadOnly:
                p.Set(3)
        except Exception:
            pass
        hidden_cats, hidden_link_count = hide_irrelevant_3d_content(
            view3d, [e.Id for e in relevant_elems]
        )
        normal_locked = ensure_3d_view_locked(view3d)
        t.Commit()
    print("CREATED 3D NORMAL:", view3d.Name, view3d.Id.IntegerValue)
    print("3D NORMAL -> locked:", normal_locked)
    print(
        "3D NORMAL -> IsLocked final:",
        view3d.IsLocked if hasattr(view3d, "IsLocked") else "n/a",
    )
    print("3D NORMAL -> hidden categories:", ", ".join(hidden_cats))
    print("3D NORMAL -> Revit links hidden:", hidden_link_count)
    print("3D NORMAL -> scanned elements shown:", len(relevant_elems))
    print("3D NORMAL -> excluded NLRS pipes:", excluded_nlrs)
    print("3D NORMAL -> included pipes:", pipe_count)
    print("3D NORMAL -> included fittings:", fitting_count)
    print("3D NORMAL -> detail level forced to Fine")
    print(
        "3D NORMAL -> final visible categories limited to Pipes/Pipe Fittings/Annotations"
    )
    return view3d, pipe_count, fitting_count, excluded_nlrs, normal_locked


def create_prefab_3d_exploded_view(doc, base_code, scanned_elems):
    normal_like, _, _, _, _ = create_prefab_3d_normal_view(
        doc, base_code, scanned_elems
    )
    with Transaction(doc, "Rename 3D Exploded View") as t:
        t.Start()
        try:
            normal_like.Name = "{} - 3D Exploded".format(base_code)
        except Exception:
            normal_like.Name = "{} - 3D Exploded ({})".format(
                base_code, normal_like.Id.IntegerValue
            )
        t.Commit()

    displacement_mode = "fallback"
    disp_set_count = 0
    displaced_count = 0
    avg_offset_mm = 0.0
    relevant_elems, filtered_scanned_pipes, filtered_scanned_fittings, excluded_nlrs = (
        get_prefab_3d_elements(scanned_elems)
    )
    pipe_count = len(filtered_scanned_pipes)
    fitting_count = len(filtered_scanned_fittings)
    displaced_pipe_count = 0
    displaced_fitting_count = 0

    groups = get_connected_prefab_components(relevant_elems)
    group_count = len(groups)
    try:
        with Transaction(doc, "Explode 3D View") as t:
            t.Start()
            vectors = []
            elem_vector_map = {}
            for i, group in enumerate(groups):
                row = i // 3
                col = i % 3
                vec = XYZ(
                    mm_to_ft((col + 1) * 3500.0),
                    -mm_to_ft((row + 1) * 2200.0),
                    0,
                )
                eid_list = List[ElementId]([e.Id for e in group])
                if eid_list.Count == 0:
                    continue
                DisplacementElement.Create(doc, eid_list, vec, normal_like, None)
                vectors.append(vec)
                disp_set_count += 1
                displaced_count += eid_list.Count
                for elem in group:
                    elem_vector_map[elem.Id.IntegerValue] = vec
                    if elem.Category.Id.IntegerValue == int(
                        BuiltInCategory.OST_PipeCurves
                    ):
                        displaced_pipe_count += 1
                    elif elem.Category.Id.IntegerValue == int(
                        BuiltInCategory.OST_PipeFitting
                    ):
                        displaced_fitting_count += 1
            if vectors:
                avg_offset_mm = sum(
                    math.sqrt(v.X * v.X + v.Y * v.Y + v.Z * v.Z) * 304.8
                    for v in vectors
                ) / float(len(vectors))
            t.Commit()
        displacement_mode = "displacement"
        _EXPLODED_VIEW_VECTORS[normal_like.Id.IntegerValue] = elem_vector_map
    except Exception as ex:
        print("3D EXPLODED -> displacement fallback used:", ex)
        displacement_mode = "fallback"
        _EXPLODED_VIEW_VECTORS[normal_like.Id.IntegerValue] = {}

    with Transaction(doc, "Lock 3D Exploded View") as t_lock:
        t_lock.Start()
        exploded_locked = ensure_3d_view_locked(normal_like)
        t_lock.Commit()

    print("CREATED 3D EXPLODED:", normal_like.Name, normal_like.Id.IntegerValue)
    print("3D EXPLODED -> locked:", exploded_locked)
    print(
        "3D EXPLODED -> IsLocked final:",
        normal_like.IsLocked if hasattr(normal_like, "IsLocked") else "n/a",
    )
    print("3D EXPLODED -> included pipes:", pipe_count)
    print("3D EXPLODED -> included fittings:", fitting_count)
    print("3D EXPLODED -> excluded NLRS pipes:", excluded_nlrs)
    print("3D EXPLODED -> connected groups:", group_count)
    print("3D EXPLODED -> mode:", displacement_mode)
    print("3D EXPLODED -> displacement sets created:", disp_set_count)
    print("3D EXPLODED -> elements displaced:", displaced_count)
    print("3D EXPLODED -> displaced pipes:", displaced_pipe_count)
    print("3D EXPLODED -> displaced fittings:", displaced_fitting_count)
    print("3D EXPLODED -> average offset magnitude mm:", round(avg_offset_mm, 1))
    print("3D EXPLODED -> detail level forced to Fine")
    print(
        "3D EXPLODED -> final visible categories limited to Pipes/Pipe Fittings/Annotations"
    )
    print("3D EXPLODED -> displacement vectors assigned:", len(groups))
    return (
        normal_like,
        displacement_mode,
        pipe_count,
        fitting_count,
        group_count,
        excluded_nlrs,
        exploded_locked,
    )


def build_pipe_label(pipe, code_map):
    code = code_map.get(pipe.Id.IntegerValue, "")
    dia = convert_param_to_string(pipe.LookupParameter("Outside Diameter"))
    if not dia:
        dia = convert_param_to_string(pipe.LookupParameter("Diameter"))
    length = convert_param_to_string(pipe.LookupParameter("Length"))
    dia = (dia or "-").replace(" mm", "").strip()
    length = (length or "-").replace(" mm", "").strip()
    prefix = "{} ".format(code) if code else ""
    return "{}{} / L={}".format(prefix, dia, length)


def get_param_text(elem, names):
    for name in names:
        try:
            p = elem.LookupParameter(name)
            if p:
                sval = p.AsString() if p.StorageType == StorageType.String else None
                if sval:
                    return sval
                v = p.AsValueString()
                if v:
                    return v
        except Exception:
            pass
        try:
            typ = doc.GetElement(elem.GetTypeId())
            if typ:
                p = typ.LookupParameter(name)
                if p:
                    sval = p.AsString() if p.StorageType == StorageType.String else None
                    if sval:
                        return sval
                    v = p.AsValueString()
                    if v:
                        return v
        except Exception:
            pass
    return ""


def build_regular_pipe_3d_text(pipe, code_map):
    return build_pipe_label(pipe, code_map)


def build_vertical_pipe_3d_text(pipe, code_map):
    dia = convert_param_to_string(
        pipe.LookupParameter("Outside Diameter")
    ) or convert_param_to_string(pipe.LookupParameter("Diameter"))
    dia = (dia or "-").replace(" mm", "").strip()
    sys_txt = get_param_text(pipe, ["System Abbreviation", "System Name", "Comments"])
    prefix = (sys_txt or "").strip().split(" ")[0]
    if prefix:
        return "{} ø{}".format(prefix, dia)
    return "ø{}".format(dia)


def build_extra_horizontal_3d_text(pipe, code_map):
    dia = convert_param_to_string(
        pipe.LookupParameter("Outside Diameter")
    ) or convert_param_to_string(pipe.LookupParameter("Diameter"))
    dia = (dia or "-").replace(" mm", "").strip()
    sys_txt = get_param_text(pipe, ["System Abbreviation", "System Name", "Comments"])
    elev_txt = get_param_text(
        pipe,
        [
            "Bottom Elevation",
            "Lower End Bottom Elevation",
            "Middle Elevation",
            "Offset",
            "Reference Level",
        ],
    )
    line1 = "{} ø{}".format((sys_txt or "-").strip().split(" ")[0], dia)
    line2 = "bb={}".format((elev_txt or "-").strip())
    return "{}\n{}".format(line1, line2)


def get_regular_pipe_tag_text(pipe, code_map):
    return build_regular_pipe_3d_text(pipe, code_map)


def get_vertical_pipe_tag_text(pipe, code_map):
    return build_vertical_pipe_3d_text(pipe, code_map)


def get_extra_horizontal_tag_text(pipe, code_map):
    return build_extra_horizontal_3d_text(pipe, code_map)


def try_lock_3d_view_for_tagging(view3d):
    try:
        if hasattr(view3d, "SaveOrientationAndLock"):
            view3d.SaveOrientationAndLock()
            return True
    except Exception as ex:
        debug("3D LOCK -> SaveOrientationAndLock failed: {}".format(ex))
    try:
        if hasattr(view3d, "IsLocked") and view3d.IsLocked:
            return True
    except Exception:
        pass
    return False


def ensure_3d_view_locked(view3d):
    try:
        if hasattr(view3d, "IsLocked"):
            try:
                if view3d.IsLocked:
                    return True
            except Exception:
                pass
        if hasattr(view3d, "SaveOrientationAndLock"):
            view3d.SaveOrientationAndLock()
            try:
                if hasattr(view3d, "IsLocked"):
                    return bool(view3d.IsLocked)
            except Exception:
                return True
        try:
            if hasattr(view3d, "IsLocked"):
                return bool(view3d.IsLocked)
        except Exception:
            pass
    except Exception as ex:
        debug("3D LOCK failed for {}: {}".format(view3d.Name, ex))
    return False


def is_vertical_arrow_tag_type(tag_type_id):
    try:
        ts = doc.GetElement(tag_type_id)
        if not ts:
            return False
        fam = ""
        typ = ""
        try:
            fam = ts.FamilyName or ""
        except Exception:
            try:
                fam = ts.Family.Name or ""
            except Exception:
                fam = ""
        try:
            typ = Element.Name.GetValue(ts) or ""
        except Exception:
            try:
                typ = ts.Name or ""
            except Exception:
                typ = ""
        fam = fam.strip().lower()
        typ = typ.strip().lower()
        return fam == "bvdk_verdiepingspijl" and typ == "van boven"
    except Exception:
        return False


def create_3d_pipe_tag(
    doc,
    view3d,
    host_elem,
    tag_type_id,
    head_pt,
    elbow_pt=None,
    end_pt=None,
    use_leader=True,
):
    try:
        ref = Reference(host_elem)
        tag = IndependentTag.Create(
            doc,
            view3d.Id,
            ref,
            use_leader,
            TagMode.TM_ADDBY_CATEGORY,
            TagOrientation.Horizontal,
            head_pt,
        )
        if tag_type_id and tag.GetTypeId().IntegerValue != tag_type_id.IntegerValue:
            tag.ChangeTypeId(tag_type_id)
        try:
            tag.HasLeader = bool(use_leader)
        except Exception:
            pass
        if elbow_pt is not None:
            try:
                tag.LeaderElbow = elbow_pt
            except Exception:
                try:
                    tag.SetLeaderElbow(ref, elbow_pt)
                except Exception:
                    pass
        if end_pt is not None:
            try:
                tag.TagHeadPosition = head_pt
            except Exception:
                pass
            try:
                tag.SetLeaderEnd(ref, end_pt)
            except Exception:
                pass
        return tag
    except Exception as ex:
        debug("3D TAG CREATE failed for {}: {}".format(host_elem.Id.IntegerValue, ex))
        return None


def _normalize_vec(vec):
    try:
        mag = math.sqrt(vec.X * vec.X + vec.Y * vec.Y + vec.Z * vec.Z)
        if mag < 1e-9:
            return XYZ(1, 0, 0)
        return XYZ(vec.X / mag, vec.Y / mag, vec.Z / mag)
    except Exception:
        return XYZ(1, 0, 0)


def get_exploded_callout_points(view3d, elem, base_mm=600, up_mm=250):
    anchor_pt = _midpoint_or_center(elem, view3d)
    vec = _EXPLODED_VIEW_VECTORS.get(view3d.Id.IntegerValue, {}).get(
        elem.Id.IntegerValue, XYZ(1, 0, 0)
    )
    dir_vec = _normalize_vec(vec)
    head_pt = XYZ(
        anchor_pt.X + dir_vec.X * mm_to_ft(base_mm),
        anchor_pt.Y + dir_vec.Y * mm_to_ft(base_mm) + mm_to_ft(up_mm),
        anchor_pt.Z + dir_vec.Z * mm_to_ft(base_mm),
    )
    elbow_pt = XYZ(
        anchor_pt.X + dir_vec.X * mm_to_ft(250),
        anchor_pt.Y + dir_vec.Y * mm_to_ft(250),
        anchor_pt.Z + dir_vec.Z * mm_to_ft(250),
    )
    return anchor_pt, head_pt, elbow_pt


def get_close_3d_tag_head(anchor_pt, dir_vec=None, is_vertical=False, is_extra=False):
    if dir_vec is None:
        dir_vec = XYZ(1, 0, 0)
    mag = math.sqrt(
        dir_vec.X * dir_vec.X + dir_vec.Y * dir_vec.Y + dir_vec.Z * dir_vec.Z
    )
    if mag < 1e-9:
        dir_vec = XYZ(1, 0, 0)
    else:
        dir_vec = XYZ(dir_vec.X / mag, dir_vec.Y / mag, dir_vec.Z / mag)
    if is_vertical:
        return XYZ(
            anchor_pt.X + mm_to_ft(180), anchor_pt.Y + mm_to_ft(120), anchor_pt.Z
        )
    if is_extra:
        return XYZ(
            anchor_pt.X + mm_to_ft(260), anchor_pt.Y + mm_to_ft(160), anchor_pt.Z
        )
    return XYZ(anchor_pt.X + mm_to_ft(300), anchor_pt.Y + mm_to_ft(180), anchor_pt.Z)


def get_curve_based_close_offset(elem):
    try:
        loc = elem.Location
        if loc and hasattr(loc, "Curve") and loc.Curve:
            p0 = loc.Curve.GetEndPoint(0)
            p1 = loc.Curve.GetEndPoint(1)
            v = p1 - p0
            mag = math.sqrt(v.X * v.X + v.Y * v.Y + v.Z * v.Z)
            if mag > 1e-9:
                vx = v.X / mag
                vy = v.Y / mag
                vz = v.Z / mag
                sx = -vy
                sy = vx
                if abs(vz) > 0.85:
                    return XYZ(mm_to_ft(120), mm_to_ft(80), 0)
                return XYZ(sx * mm_to_ft(180), sy * mm_to_ft(180), mm_to_ft(60))
    except Exception:
        pass
    return XYZ(mm_to_ft(180), mm_to_ft(140), mm_to_ft(60))


def get_vertical_pipe_anchor_pt(elem, view):
    try:
        loc = elem.Location
        if loc and hasattr(loc, "Curve") and loc.Curve:
            p0 = loc.Curve.GetEndPoint(0)
            p1 = loc.Curve.GetEndPoint(1)
            return p0 if p0.Z >= p1.Z else p1
    except Exception:
        pass
    try:
        bb = elem.get_BoundingBox(view)
        if bb:
            return XYZ(
                (bb.Min.X + bb.Max.X) * 0.5, (bb.Min.Y + bb.Max.Y) * 0.5, bb.Max.Z
            )
    except Exception:
        pass
    return _midpoint_or_center(elem, view)


def get_vertical_tag_head_pt(anchor_pt):
    return XYZ(anchor_pt.X, anchor_pt.Y, anchor_pt.Z)


def get_exploded_visible_anchor_pt(elem, view):
    try:
        bb = elem.get_BoundingBox(view)
        if bb:
            return XYZ(
                (bb.Min.X + bb.Max.X) * 0.5,
                (bb.Min.Y + bb.Max.Y) * 0.5,
                (bb.Min.Z + bb.Max.Z) * 0.5,
            )
    except Exception:
        pass
    return _midpoint_or_center(elem, view)


def get_exploded_tag_head_pt(
    anchor_pt, elem, is_vertical_tag=False, is_extra_tag=False
):
    if is_vertical_tag:
        return get_vertical_tag_head_pt(anchor_pt)
    if is_extra_tag:
        return get_exploded_extra_tag_head_pt(anchor_pt, elem)
    return get_exploded_regular_tag_head_pt(anchor_pt, elem)


def clamp_head_near_anchor(anchor_pt, head_pt, max_mm):
    dx = head_pt.X - anchor_pt.X
    dy = head_pt.Y - anchor_pt.Y
    dz = head_pt.Z - anchor_pt.Z
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    maxd = mm_to_ft(max_mm)
    if dist <= maxd or dist < 1e-9:
        return head_pt
    s = maxd / dist
    return XYZ(anchor_pt.X + dx * s, anchor_pt.Y + dy * s, anchor_pt.Z + dz * s)


def get_curve_direction_xy(elem):
    try:
        loc = elem.Location
        if loc and hasattr(loc, "Curve") and loc.Curve:
            p0 = loc.Curve.GetEndPoint(0)
            p1 = loc.Curve.GetEndPoint(1)
            vx = p1.X - p0.X
            vy = p1.Y - p0.Y
            mag = math.sqrt(vx * vx + vy * vy)
            if mag > 1e-9:
                return XYZ(vx / mag, vy / mag, 0)
    except Exception:
        pass
    return XYZ(1, 0, 0)


def get_short_side_vector_xy(dir_xy):
    return XYZ(-dir_xy.Y, dir_xy.X, 0)


def get_exploded_regular_tag_points(elem, view):
    anchor_pt = get_exploded_visible_anchor_pt(elem, view)
    dir_xy = get_curve_direction_xy(elem)
    side = get_short_side_vector_xy(dir_xy)
    side_mm = 120.0
    up_mm = 35.0
    head_pt = XYZ(
        anchor_pt.X + side.X * mm_to_ft(side_mm),
        anchor_pt.Y + side.Y * mm_to_ft(side_mm),
        anchor_pt.Z + mm_to_ft(up_mm),
    )
    return anchor_pt, head_pt


def get_exploded_extra_tag_points(elem, view):
    anchor_pt = get_exploded_visible_anchor_pt(elem, view)
    try:
        dir_xy = get_curve_direction_xy(elem)
        side = get_short_side_vector_xy(dir_xy)
    except Exception:
        side = XYZ(1, 0, 0)
    head_pt = XYZ(
        anchor_pt.X + side.X * mm_to_ft(110.0),
        anchor_pt.Y + side.Y * mm_to_ft(110.0),
        anchor_pt.Z + mm_to_ft(30.0),
    )
    return anchor_pt, head_pt


def get_exploded_regular_tag_head_pt(anchor_pt, elem):
    dir_xy = get_curve_direction_xy(elem)
    side = get_short_side_vector_xy(dir_xy)
    return XYZ(
        anchor_pt.X + side.X * mm_to_ft(120.0),
        anchor_pt.Y + side.Y * mm_to_ft(120.0),
        anchor_pt.Z + mm_to_ft(35.0),
    )


def get_exploded_extra_tag_head_pt(anchor_pt, elem):
    try:
        dir_xy = get_curve_direction_xy(elem)
        side = get_short_side_vector_xy(dir_xy)
    except Exception:
        side = XYZ(1, 0, 0)
    return XYZ(
        anchor_pt.X + side.X * mm_to_ft(110.0),
        anchor_pt.Y + side.Y * mm_to_ft(110.0),
        anchor_pt.Z + mm_to_ft(30.0),
    )


def get_close_3d_tag_points(view3d, elem, is_vertical=False, is_extra=False):
    anchor_pt = (
        get_vertical_pipe_anchor_pt(elem, view3d)
        if is_vertical
        else _midpoint_or_center(elem, view3d)
    )
    dir_vec = _EXPLODED_VIEW_VECTORS.get(view3d.Id.IntegerValue, {}).get(
        elem.Id.IntegerValue, XYZ(1, 0, 0)
    )
    if "Exploded" in view3d.Name:
        if is_vertical:
            head_pt = get_vertical_tag_head_pt(anchor_pt)
            elbow_pt = anchor_pt
            return anchor_pt, head_pt, elbow_pt
        if is_extra:
            head_pt = get_exploded_extra_tag_head_pt(anchor_pt, elem)
            head_pt = clamp_head_near_anchor(anchor_pt, head_pt, 180.0)
        else:
            head_pt = get_exploded_regular_tag_head_pt(anchor_pt, elem)
            head_pt = clamp_head_near_anchor(anchor_pt, head_pt, 220.0)
    else:
        head_pt = (
            get_vertical_tag_head_pt(anchor_pt)
            if is_vertical
            else get_close_3d_tag_head(
                anchor_pt, dir_vec=dir_vec, is_vertical=is_vertical, is_extra=is_extra
            )
        )
    dir_vec = _normalize_vec(dir_vec)
    elbow_pt = XYZ(
        anchor_pt.X + dir_vec.X * mm_to_ft(120),
        anchor_pt.Y + dir_vec.Y * mm_to_ft(120),
        anchor_pt.Z + dir_vec.Z * mm_to_ft(120),
    )
    if is_vertical:
        elbow_pt = anchor_pt
    return anchor_pt, head_pt, elbow_pt


def annotate_exploded_3d_with_real_tags(doc, view3d, scanned_elems, code_map):
    attempted_tag_count = 0
    successful_real_tag_count = 0
    failed_real_tag_count = 0
    excluded_nlrs_pipe_count = 0
    vertical_tag_count = 0
    regular_tag_count = 0
    extra_tag_count = 0
    sample_head_distances = []
    pipes = []
    for e in scanned_elems:
        if (
            e
            and e.Category
            and e.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeCurves)
        ):
            if is_excluded_nlrs_pipe(e):
                excluded_nlrs_pipe_count += 1
                continue
            pipes.append(e)
    if not pipes:
        return {
            "tags": 0,
            "mode": "none",
            "excluded_nlrs": excluded_nlrs_pipe_count,
            "attempted": 0,
            "failed": 0,
            "vertical_real": 0,
            "regular_real": 0,
            "extra_real": 0,
            "sample_head_distances": [],
        }
    view_locked = False
    try:
        view_locked = bool(view3d.IsLocked) if hasattr(view3d, "IsLocked") else False
    except Exception:
        view_locked = False
    print(
        "3D EXPLODED -> view:",
        view3d.Name,
        view3d.Id.IntegerValue,
        "locked:",
        view_locked,
    )
    if not view_locked:
        print("3D TAGGING SKIPPED -> view not locked:", view3d.Name)
        return {
            "tags": 0,
            "mode": "skipped",
            "excluded_nlrs": excluded_nlrs_pipe_count,
            "attempted": 0,
            "failed": 0,
            "vertical_real": 0,
            "regular_real": 0,
            "extra_real": 0,
            "sample_head_distances": [],
        }
    resolved, _ = resolve_required_tag_symbols(doc)
    sym_regular = choose_best_symbol_for_family(
        doc, "RSen_GA_tag pipe_ACO", ["TypeMark + DN + ø"]
    )
    sym_vertical = resolved.get("VERT_ARROW")
    sym_extra = resolved.get("EXTRA_HORIZONTAL")
    extra_targets, _ = find_extra_horizontal_targets(pipes)
    print("3D EXPLODED -> extra horizontal-connected targets:", len(extra_targets))
    with Transaction(doc, "Annotate Exploded 3D With Real Tags") as t:
        t.Start()
        for pipe in pipes:
            labels = []
            if is_vertical_pipe(pipe):
                labels.append(("vertical", sym_vertical.Id if sym_vertical else None))
                vertical_tag_count += 1
            else:
                labels.append(("regular", sym_regular.Id if sym_regular else None))
                regular_tag_count += 1
            if pipe.Id.IntegerValue in extra_targets:
                labels.append(("extra", sym_extra.Id if sym_extra else None))
                extra_tag_count += 1
            for label_kind, tag_type_id in labels:
                attempted_tag_count += 1
                if tag_type_id is None:
                    failed_real_tag_count += 1
                    continue
                if label_kind == "vertical":
                    anchor_pt = get_vertical_pipe_anchor_pt(pipe, view3d)
                    head_pt = get_vertical_tag_head_pt(anchor_pt)
                elif label_kind == "extra":
                    anchor_pt, head_pt = get_exploded_extra_tag_points(pipe, view3d)
                    head_pt = clamp_head_near_anchor(anchor_pt, head_pt, 180.0)
                else:
                    anchor_pt, head_pt = get_exploded_regular_tag_points(pipe, view3d)
                    head_pt = clamp_head_near_anchor(anchor_pt, head_pt, 220.0)
                print(
                    "3D EXPLODED TAG -> host={} type_id={} point=({}, {}, {})".format(
                        pipe.Id.IntegerValue,
                        tag_type_id.IntegerValue,
                        round(head_pt.X, 4),
                        round(head_pt.Y, 4),
                        round(head_pt.Z, 4),
                    )
                )
                tag = create_3d_pipe_tag(
                    doc,
                    view3d,
                    pipe,
                    tag_type_id,
                    head_pt,
                    elbow_pt=None,
                    end_pt=None,
                    use_leader=False,
                )
                if tag is None:
                    failed_real_tag_count += 1
                    continue
                try:
                    tag.HasLeader = False
                except Exception:
                    pass
                try:
                    tag.TagHeadPosition = head_pt
                except Exception:
                    pass
                successful_real_tag_count += 1
                dist_mm = (
                    math.sqrt(
                        (head_pt.X - anchor_pt.X) * (head_pt.X - anchor_pt.X)
                        + (head_pt.Y - anchor_pt.Y) * (head_pt.Y - anchor_pt.Y)
                        + (head_pt.Z - anchor_pt.Z) * (head_pt.Z - anchor_pt.Z)
                    )
                    * 304.8
                )
                if len(sample_head_distances) < 5:
                    sample_head_distances.append(round(dist_mm, 1))
                if label_kind == "vertical":
                    print("3D TAG -> vertical on-host:", pipe.Id.IntegerValue)
        t.Commit()
    print("FINAL 3D EXPLODED -> locked:", view_locked)
    print("FINAL 3D EXPLODED -> attempted tag count:", attempted_tag_count)
    print(
        "FINAL 3D EXPLODED -> successful real 3D tag count:", successful_real_tag_count
    )
    print("FINAL 3D EXPLODED -> failed real 3D tag count:", failed_real_tag_count)
    print("FINAL 3D EXPLODED -> excluded NLRS pipe count:", excluded_nlrs_pipe_count)
    print("FINAL 3D EXPLODED -> vertical tag count:", vertical_tag_count)
    print("FINAL 3D EXPLODED -> regular tag count:", regular_tag_count)
    return {
        "tags": successful_real_tag_count,
        "mode": "tags" if successful_real_tag_count > 0 else "none",
        "excluded_nlrs": excluded_nlrs_pipe_count,
        "attempted": attempted_tag_count,
        "failed": failed_real_tag_count,
        "vertical_real": vertical_tag_count,
        "regular_real": regular_tag_count,
        "extra_real": extra_tag_count,
        "sample_head_distances": sample_head_distances,
    }


def annotate_prefab_3d_view(doc, uidoc, view3d, scanned_elems, code_map):
    regular_count = 0
    vertical_count = 0
    extra_count = 0
    regular_real_count = 0
    vertical_real_count = 0
    extra_real_count = 0
    vertical_no_leader_count = 0
    regular_leader_count = 0
    extra_leader_count = 0
    sample_head_distances = []
    max_regular_mm = 0.0
    max_extra_mm = 0.0
    pipes = [
        e
        for e in scanned_elems
        if e
        and e.Category
        and e.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeCurves)
        and not is_excluded_nlrs_pipe(e)
    ]
    if not pipes:
        return {"tags": 0, "text": 0, "mode": "none"}
    extra_targets, _ = find_extra_horizontal_targets(pipes)
    if "Exploded" in view3d.Name:
        print("3D EXPLODED -> extra horizontal-connected targets:", len(extra_targets))
    else:
        print("3D NORMAL -> extra horizontal-connected targets:", len(extra_targets))
    view_locked = False
    try:
        view_locked = bool(view3d.IsLocked) if hasattr(view3d, "IsLocked") else False
    except Exception:
        view_locked = False
    if not view_locked:
        print("3D TAGGING SKIPPED -> view not locked:", view3d.Name)
        return {"tags": 0, "text": 0, "mode": "skipped"}
    resolved, _ = resolve_required_tag_symbols(doc)
    sym_regular = choose_best_symbol_for_family(
        doc, "RSen_GA_tag pipe_ACO", ["TypeMark + DN + ø"]
    )
    sym_vertical = resolved.get("VERT_ARROW")
    sym_extra = resolved.get("EXTRA_HORIZONTAL")
    with Transaction(doc, "Annotate 3D View") as t:
        t.Start()
        for idx, pipe in enumerate(pipes):
            labels = []
            if is_vertical_pipe(pipe):
                labels.append(
                    (
                        "vertical",
                        get_vertical_pipe_tag_text(pipe, code_map),
                    )
                )
                vertical_count += 1
            else:
                labels.append(
                    (
                        "regular",
                        get_regular_pipe_tag_text(pipe, code_map),
                    )
                )
                regular_count += 1
            if pipe.Id.IntegerValue in extra_targets:
                labels.append(
                    (
                        "extra",
                        get_extra_horizontal_tag_text(pipe, code_map),
                    )
                )
                extra_count += 1
            for label_kind, _label in labels:
                anchor_pt, head_pt, elbow_pt = get_close_3d_tag_points(
                    view3d,
                    pipe,
                    is_vertical=(label_kind == "vertical"),
                    is_extra=(label_kind == "extra"),
                )
                tag_type_id = None
                if label_kind == "regular" and sym_regular is not None:
                    tag_type_id = sym_regular.Id
                elif label_kind == "vertical" and sym_vertical is not None:
                    tag_type_id = sym_vertical.Id
                elif label_kind == "extra" and sym_extra is not None:
                    tag_type_id = sym_extra.Id
                if tag_type_id is None:
                    continue
                use_leader = not is_vertical_arrow_tag_type(tag_type_id)
                created_real = create_3d_pipe_tag(
                    doc,
                    view3d,
                    pipe,
                    tag_type_id,
                    head_pt,
                    elbow_pt=elbow_pt,
                    end_pt=anchor_pt,
                    use_leader=use_leader,
                )
                if created_real is None:
                    continue
                if is_vertical_arrow_tag_type(tag_type_id):
                    try:
                        created_real.HasLeader = False
                    except Exception:
                        pass
                    try:
                        created_real.TagHeadPosition = head_pt
                    except Exception:
                        pass
                dist_mm = (
                    math.sqrt(
                        (head_pt.X - anchor_pt.X) * (head_pt.X - anchor_pt.X)
                        + (head_pt.Y - anchor_pt.Y) * (head_pt.Y - anchor_pt.Y)
                        + (head_pt.Z - anchor_pt.Z) * (head_pt.Z - anchor_pt.Z)
                    )
                    * 304.8
                )
                if "Exploded" in view3d.Name and len(sample_head_distances) < 5:
                    sample_head_distances.append(round(dist_mm, 1))
                if label_kind == "regular":
                    regular_real_count += 1
                    regular_leader_count += 1
                    max_regular_mm = max(max_regular_mm, dist_mm)
                elif label_kind == "vertical":
                    vertical_real_count += 1
                    vertical_no_leader_count += 1
                    print("3D TAG -> vertical no-leader:", pipe.Id.IntegerValue)
                    print("3D TAG -> vertical on-host:", pipe.Id.IntegerValue)
                elif label_kind == "extra":
                    extra_real_count += 1
                    extra_leader_count += 1
                    max_extra_mm = max(max_extra_mm, dist_mm)
        t.Commit()
    mode = (
        "tags"
        if (regular_real_count + vertical_real_count + extra_real_count) > 0
        else "none"
    )
    if "Exploded" in view3d.Name:
        print("3D EXPLODED -> text annotations regular:", 0)
        print("3D EXPLODED -> text annotations vertical:", 0)
        print("3D EXPLODED -> text annotations extra:", 0)
        print("3D EXPLODED -> real 3D regular tags:", regular_real_count)
        print("3D EXPLODED -> real 3D vertical tags:", vertical_real_count)
        print("3D EXPLODED -> real 3D extra tags:", extra_real_count)
        print("3D EXPLODED -> fallback text regular:", 0)
        print("3D EXPLODED -> fallback text vertical:", 0)
        print("3D EXPLODED -> fallback text extra:", 0)
        print("FINAL 3D EXPLODED -> locked:", view_locked)
        print("FINAL 3D EXPLODED -> real regular tags:", regular_real_count)
        print("FINAL 3D EXPLODED -> real vertical tags:", vertical_real_count)
        print("FINAL 3D EXPLODED -> real extra tags:", extra_real_count)
        print("FINAL 3D EXPLODED -> fallback regular text:", 0)
        print("FINAL 3D EXPLODED -> fallback vertical text:", 0)
        print("FINAL 3D EXPLODED -> fallback extra text:", 0)
        print("3D EXPLODED -> vertical no-leader tags:", vertical_no_leader_count)
        print("3D EXPLODED -> regular leader tags:", regular_leader_count)
        print("3D EXPLODED -> extra leader tags:", extra_leader_count)
        print("EXPLODED TAG PATCH -> regular tags:", regular_real_count)
        print("EXPLODED TAG PATCH -> extra tags:", extra_real_count)
        print(
            "EXPLODED TAG PATCH -> vertical no-leader tags:", vertical_no_leader_count
        )
        print(
            "EXPLODED TAG PATCH -> max regular head distance mm:",
            round(max_regular_mm, 1),
        )
        print(
            "EXPLODED TAG PATCH -> max extra head distance mm:", round(max_extra_mm, 1)
        )
        for dist_mm in sample_head_distances:
            print("3D EXPLODED -> sample head distance mm:", dist_mm)
            print("EXPLODED TAG PATCH -> sample regular distance mm:", dist_mm)
    else:
        print("3D NORMAL -> vertical no-leader tags:", vertical_no_leader_count)
    print("3D ANNOTATE -> view:", view3d.Name, view3d.Id.IntegerValue, "mode:", mode)
    return {
        "tags": regular_real_count + vertical_real_count + extra_real_count,
        "text": 0,
        "mode": mode,
        "regular_real": regular_real_count,
        "vertical_real": vertical_real_count,
        "extra_real": extra_real_count,
        "vertical_no_leader": vertical_no_leader_count,
        "vertical_onhost": vertical_no_leader_count,
        "regular_leader": regular_leader_count,
        "extra_leader": extra_leader_count,
        "sample_head_distances": sample_head_distances,
        "max_regular_mm": round(max_regular_mm, 1),
        "max_extra_mm": round(max_extra_mm, 1),
    }


def place_prefab_views_on_sheet(doc, sheet, plan_view, view3d_normal, view3d_exploded):
    min_u = sheet.Outline.Min.U
    max_u = sheet.Outline.Max.U
    min_v = sheet.Outline.Min.V
    max_v = sheet.Outline.Max.V
    width = max_u - min_u
    height = max_v - min_v
    plan_pt = XYZ(min_u + width * 0.28, min_v + height * 0.72, 0)
    normal_pt = XYZ(min_u + width * 0.72, min_v + height * 0.72, 0)
    exploded_pt = XYZ(min_u + width * 0.72, min_v + height * 0.32, 0)

    if not Viewport.CanAddViewToSheet(doc, sheet.Id, plan_view.Id):
        raise Exception(
            "Plan view cannot be placed on sheet: {}".format(plan_view.Name)
        )
    if not Viewport.CanAddViewToSheet(doc, sheet.Id, view3d_normal.Id):
        raise Exception(
            "3D normal view cannot be placed on sheet: {}".format(view3d_normal.Name)
        )
    if not Viewport.CanAddViewToSheet(doc, sheet.Id, view3d_exploded.Id):
        raise Exception(
            "3D exploded view cannot be placed on sheet: {}".format(
                view3d_exploded.Name
            )
        )

    Viewport.Create(doc, sheet.Id, plan_view.Id, plan_pt)
    print(
        "SHEET PLACE -> floor plan viewport placed:",
        plan_view.Name,
        plan_view.Id.IntegerValue,
    )
    Viewport.Create(doc, sheet.Id, view3d_normal.Id, normal_pt)
    print(
        "SHEET PLACE -> 3D normal viewport placed:",
        view3d_normal.Name,
        view3d_normal.Id.IntegerValue,
    )
    Viewport.Create(doc, sheet.Id, view3d_exploded.Id, exploded_pt)
    print(
        "SHEET PLACE -> 3D exploded viewport placed:",
        view3d_exploded.Name,
        view3d_exploded.Id.IntegerValue,
    )


def apply_prefab_changes_to_view(
    doc, uidoc, target_view, result, polygon, gathered_elements
):
    """
    Must:
    - place/update prefab text note in target_view
    - add/remove pipe tags in target_view
    - not create sheet
    - not duplicate any view
    - commit changes in a real transaction
    - return counts/debug info
    """
    print("APPLY PREFAB -> transaction start")
    print(
        "APPLY PREFAB -> target view:",
        target_view.Name,
        target_view.Id.IntegerValue,
    )
    scanned_pipes = []
    scanned_pipe_ids = set()
    for e in gathered_elements:
        if (
            e
            and e.Category
            and e.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeCurves)
            and not is_excluded_nlrs_pipe(e)
        ):
            scanned_pipes.append(e)
            scanned_pipe_ids.add(e.Id.IntegerValue)

    print("APPLY PREFAB -> scanned pipe ids count:", len(scanned_pipe_ids))
    resolved, missing_types = resolve_required_tag_symbols(doc)
    sym_aco = resolved.get("ACO_PIPE")
    sym_vert = resolved.get("VERT_ARROW")
    sym_extra = resolved.get("EXTRA_HORIZONTAL")

    created_count = 0
    kept_count = 0
    recreated_count = 0
    deleted_count = 0
    remove_count = 0
    created_extra_count = 0
    failures = []
    text_note_done = False
    original_template_id = None
    template_detached = False
    t = Transaction(doc, "Apply prefab tags/text")
    try:
        t.Start()

        try:
            original_template_id = target_view.ViewTemplateId
        except Exception:
            original_template_id = ElementId.InvalidElementId
        print(
            "APPLY ONLY -> original template id:",
            original_template_id.IntegerValue if original_template_id else -1,
        )
        try:
            if (
                original_template_id
                and original_template_id != ElementId.InvalidElementId
            ):
                target_view.ViewTemplateId = ElementId.InvalidElementId
                template_detached = True
                print("APPLY ONLY -> template detached for tagging")
        except Exception:
            pass

        raw_text = (result.get("TextNote", "") or "").strip()
        if raw_text:
            pminx, pminy, _, _ = polygon_bounds_xy(polygon)
            note_pt = XYZ(pminx + XY_PAD, pminy + XY_PAD, 0)
            prefab_re = re.compile(r"\d+(?:\.\d+)+")
            notes = (
                FilteredElementCollector(doc, target_view.Id)
                .OfClass(TextNote)
                .WhereElementIsNotElementType()
                .ToElements()
            )
            target_note = None
            for n in notes:
                try:
                    txt = n.Text or ""
                    p = n.Coord
                    if prefab_re.search(txt) and is_point_inside_polygon(
                        XYZ(p.X, p.Y, 0), polygon
                    ):
                        target_note = n
                        break
                except Exception:
                    continue
            if target_note:
                target_note.Text = raw_text
                text_note_done = True
            else:
                nt = FilteredElementCollector(doc).OfClass(TextNoteType).FirstElement()
                if nt:
                    TextNote.Create(
                        doc, target_view.Id, note_pt, raw_text, TextNoteOptions(nt.Id)
                    )
                    text_note_done = True

        existing_by_host = collect_pipe_tags_by_host(target_view, scanned_pipe_ids)
        extra_targets, verticals = find_extra_horizontal_targets(scanned_pipes)
        print("FLOOR PLAN -> vertical pipes:", len(verticals))
        print("FLOOR PLAN -> extra horizontal-connected targets:", len(extra_targets))
        print(
            "FLOOR PLAN -> extra horizontal target ids:",
            sorted(list(extra_targets))[:20],
        )
        print(
            "APPLY PREFAB -> existing mapped pipe tags count:",
            len(existing_by_host),
        )
        for p in scanned_pipes:
            desired_kind = resolve_pipe_tag_kind(p)
            host_tags = existing_by_host.get(p.Id.IntegerValue, [])
            matching_tag = None
            extra_tags = []
            for tag in host_tags:
                if matching_tag is None and tag_matches_kind(tag, desired_kind):
                    matching_tag = tag
                else:
                    extra_tags.append(tag)
            for extra_tag in extra_tags:
                try:
                    doc.Delete(extra_tag.Id)
                    deleted_count += 1
                except Exception:
                    pass
            if matching_tag is not None:
                kept_count += 1
                continue
            is_vert = desired_kind == "VERT_ARROW"
            if is_vert:
                sym = sym_vert
                use_leader = bool(TAGCFG_VERD.get("leader", False))
                use_offset_mm = TAGCFG_VERD.get("offset_mm", 0)
            else:
                sym = sym_aco
                use_leader = bool(TAGCFG_ACO.get("leader", True))
                use_offset_mm = TAGCFG_ACO.get("offset_mm", 400)
            if sym is None:
                failures.append(p.Id.IntegerValue)
                continue
            for old_tag in host_tags:
                try:
                    doc.Delete(old_tag.Id)
                    deleted_count += 1
                except Exception:
                    pass
            try:
                pt = _midpoint_or_center(p, target_view)
                tag = IndependentTag.Create(
                    doc,
                    target_view.Id,
                    Reference(p),
                    use_leader,
                    TagMode.TM_ADDBY_CATEGORY,
                    TagOrientation.Horizontal,
                    pt,
                )
                tag.ChangeTypeId(sym.Id)
                if use_leader:
                    try:
                        tag.HasLeader = True
                    except Exception:
                        pass
                if use_offset_mm and use_offset_mm > 0:
                    off = view_offset_vector(target_view, use_offset_mm)
                    try:
                        tag.TagHeadPosition = XYZ(
                            pt.X + off.X, pt.Y + off.Y, pt.Z + off.Z
                        )
                    except Exception:
                        pass
                if host_tags:
                    recreated_count += 1
                else:
                    created_count += 1
            except Exception as ex:
                print(
                    "APPLY PREFAB -> tag create failed for {}: {}".format(
                        p.Id.IntegerValue, ex
                    )
                )
                failures.append(p.Id.IntegerValue)

        for host_id in sorted(extra_targets):
            pipe = doc.GetElement(ElementId(host_id))
            if not pipe:
                continue
            existing_extra = find_specific_tag_on_host_in_view(
                doc,
                target_view,
                pipe.Id,
                TAGCFG_EXTRA_H["family"],
                TAGCFG_EXTRA_H["type"],
            )
            if existing_extra is not None:
                continue
            if sym_extra is None:
                continue
            try:
                pt = _midpoint_or_center(pipe, target_view)
                extra_tag = IndependentTag.Create(
                    doc,
                    target_view.Id,
                    Reference(pipe),
                    bool(TAGCFG_EXTRA_H.get("leader", True)),
                    TagMode.TM_ADDBY_CATEGORY,
                    TagOrientation.Horizontal,
                    pt,
                )
                extra_tag.ChangeTypeId(sym_extra.Id)
                off = view_offset_vector(
                    target_view, TAGCFG_EXTRA_H.get("offset_mm", 350)
                )
                try:
                    extra_tag.HasLeader = True
                except Exception:
                    pass
                try:
                    extra_tag.TagHeadPosition = XYZ(
                        pt.X + off.X, pt.Y + off.Y, pt.Z + off.Z
                    )
                except Exception:
                    pass
                created_extra_count += 1
            except Exception as ex:
                print(
                    "FLOOR PLAN -> extra horizontal tag create failed for {}: {}".format(
                        host_id, ex
                    )
                )

        doc.Regenerate()
        print(
            "APPLY PREFAB -> counts before commit: keep={}, create={}, recreate={}, delete={}, remove={}".format(
                kept_count, created_count, recreated_count, deleted_count, remove_count
            )
        )
        print("FLOOR PLAN -> extra horizontal tag created:", created_extra_count)
        t.Commit()
        print("APPLY PREFAB -> transaction committed")
    except Exception:
        try:
            if t.HasStarted():
                t.RollBack()
        except Exception:
            pass
        raise

    post_count = count_pipe_tags_in_view(doc, target_view)
    print("APPLY PREFAB -> pipe tags visible in target view after commit:", post_count)
    print("APPLY PREFAB -> kept tags count:", kept_count)
    print("APPLY PREFAB -> recreated tags count:", recreated_count)
    print("APPLY PREFAB -> created tags count:", created_count)
    print("APPLY PREFAB -> deleted tags count:", deleted_count)
    print("FLOOR PLAN -> extra horizontal tag created:", created_extra_count)
    if template_detached:
        print("APPLY ONLY -> template left detached after apply-only")

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

    return {
        "target_view_name": target_view.Name,
        "target_view_id": target_view.Id.IntegerValue,
        "kept_tags": kept_count,
        "added_tags": created_count,
        "recreated_tags": recreated_count,
        "removed_tags": remove_count,
        "deleted_tags": deleted_count,
        "text_note_placed": text_note_done,
        "template_detached": template_detached,
        "scanned_pipe_ids": sorted(list(scanned_pipe_ids)),
        "extra_horizontal_tags_created": created_extra_count,
    }


def main():
    scanned_source_view = uidoc.ActiveView
    scanned_source_view_id = scanned_source_view.Id
    print(
        "SCANNED SOURCE VIEW:",
        scanned_source_view.Name,
        scanned_source_view_id.IntegerValue,
    )
    gathered_elements, polygon = select_boundary_and_gather()
    if not gathered_elements:
        MessageBox.Show("No elements were gathered. Cancelled.", "Error")
        return

    filtered = filter_relevant_elements(gathered_elements)
    if not filtered:
        MessageBox.Show("No relevant elements found in region.", "Error")
        return

    result = show_element_editor(
        filtered, region_elements=gathered_elements, region_polygon=polygon
    )
    if result is None:
        return

    workflow_choice = choose_post_editor_workflow()
    if workflow_choice == "cancel":
        return

    uidoc.Selection.SetElementIds(List[ElementId]())

    baseCode = result["TextNote"]
    for eData in result["Elements"]:
        if eData["Category"] == "Pipe Fittings":
            eData["NewCode"] = baseCode

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

    source_view = scanned_source_view
    if not isinstance(source_view, ViewPlan):
        MessageBox.Show("Active view is not a plan/callout view.", "Error")
        return

    resolved_syms, missing_req = resolve_required_tag_symbols(doc)
    if missing_req:
        TaskDialog.Show(
            "Missing Pipe Tag Types",
            "Could not find required tag types:\n- " + "\n- ".join(missing_req),
        )

    apply_info = apply_prefab_changes_to_view(
        doc, uidoc, source_view, result, polygon, gathered_elements
    )

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
        target_view = source_view
        try:
            if uidoc.ActiveView.Id != target_view.Id:
                uidoc.ActiveView = target_view
        except Exception as ex:
            print("APPLY ONLY -> failed to activate target view:", ex)

        scanned_pipe_ids = set(apply_info.get("scanned_pipe_ids", []))
        final_tag_ids = get_pipe_tag_ids_for_hosts_in_view(
            doc, target_view, scanned_pipe_ids
        )
        print("APPLY ONLY -> before success dialog")
        try:
            print(
                "APPLY ONLY -> active view now:",
                uidoc.ActiveView.Name,
                uidoc.ActiveView.Id.IntegerValue,
            )
        except Exception as ex:
            print("APPLY ONLY -> active view read failed:", ex)
        print(
            "APPLY ONLY -> target view now:",
            target_view.Name,
            target_view.Id.IntegerValue,
        )
        print("APPLY ONLY -> tag ids before popup:", final_tag_ids)
        print("APPLY ONLY -> final tag count:", len(final_tag_ids))
        try:
            print(
                "APPLY ONLY -> target view template id:",
                target_view.ViewTemplateId.IntegerValue,
            )
            print("APPLY ONLY -> target view is template:", target_view.IsTemplate)
        except Exception as ex:
            print("APPLY ONLY -> template info read failed:", ex)
        pipe_tag_cat = Category.GetCategory(doc, BuiltInCategory.OST_PipeTags)
        if pipe_tag_cat:
            try:
                print(
                    "APPLY ONLY -> pipe tag category hidden:",
                    target_view.GetCategoryHidden(pipe_tag_cat.Id),
                )
            except Exception as ex:
                print("APPLY ONLY -> failed hidden check:", ex)

        TaskDialog.Show(
            "Bunge Sheet Gen",
            "Prefab text and tags were applied to the current scanned view:\n{}\n\nKept tags: {}\nAdded tags: {}\nRemoved tags: {}\nText note placed: {}\nTemplate detached: {}".format(
                target_view.Name,
                apply_info.get("kept_tags", 0),
                apply_info.get("added_tags", 0),
                apply_info.get("removed_tags", 0),
                apply_info.get("text_note_placed", False),
                apply_info.get("template_detached", False),
            ),
        )
        post_popup_tag_ids = get_pipe_tag_ids_for_hosts_in_view(
            doc, target_view, scanned_pipe_ids
        )
        print("APPLY ONLY -> success dialog closed")
        print("APPLY ONLY -> tag ids after popup returns:", post_popup_tag_ids)
        print("APPLY ONLY -> tag count after popup returns:", len(post_popup_tag_ids))
        try:
            print(
                "APPLY ONLY -> final template id before return:",
                target_view.ViewTemplateId.IntegerValue,
            )
        except Exception as ex:
            print("APPLY ONLY -> final template id read failed:", ex)
        if post_popup_tag_ids:
            try:
                uidoc.ActiveView = target_view
            except Exception as ex:
                print("APPLY ONLY -> failed to reactivate target view:", ex)
            try:
                uidoc.RefreshActiveView()
            except Exception as ex:
                print("APPLY ONLY -> refresh active view failed:", ex)
        print("APPLY ONLY -> returning cleanly from main")
        return

    m = re.search(r"([\d\.]+)", result["TextNote"])
    base = m.group(1) if m else (result["TextNote"] or "").strip()

    all_tbs = list(
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_TitleBlocks)
        .OfClass(FamilySymbol)
        .ToElements()
    )
    if not all_tbs:
        MessageBox.Show("No title-block types found.", "Error")
        return

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
                    sym.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM).AsString()
                    or ""
                )
                self.lb.Items.Add(fam + " - " + type_name)
            self.Controls.Add(self.lb)
            ok = Button(
                Text="OK", DialogResult=DialogResult.OK, Location=Point(10, 320)
            )
            ca = Button(
                Text="Cancel",
                DialogResult=DialogResult.Cancel,
                Location=Point(100, 320),
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
        return

    title_block = all_tbs[picker.lb.SelectedIndex]
    sheet_code = base
    if sheet_code in existing_numbers:
        sheet_code = "{} ({})".format(base, source_view.Id.IntegerValue)

    try:
        if hasattr(title_block, "IsActive") and (not title_block.IsActive):
            with Transaction(doc, "Activate title block") as t_act:
                t_act.Start()
                title_block.Activate()
                t_act.Commit()
    except:
        pass

    try:
        target_view = source_view
        print(
            "SHEET MODE -> using existing scanned source view:",
            target_view.Name,
            target_view.Id.IntegerValue,
        )
        print("SHEET MODE -> source view type:", target_view.ViewType)
        print(
            "SHEET MODE -> source view template id:",
            target_view.ViewTemplateId.IntegerValue,
        )
        try:
            print(
                "SHEET MODE -> primary view id:",
                target_view.GetPrimaryViewId().IntegerValue,
            )
        except Exception:
            print("SHEET MODE -> primary view id: n/a")

        scanned_pipe_ids = apply_info.get("scanned_pipe_ids", [])
        pre_sheet_tag_count = count_pipe_tags_for_scanned_pipes(
            doc, target_view, scanned_pipe_ids
        )
        print(("PRE-SHEET SOURCE PIPE TAG COUNT:", pre_sheet_tag_count))
        if pre_sheet_tag_count != len(scanned_pipe_ids):
            print(
                "WARNING: source scanned view does not contain all expected pipe tags before clone"
            )

        clone_name = "{} - Sheet".format(sheet_code)
        sheet_view = create_sheet_safe_plan_from_scanned_view(
            doc, target_view, clone_name
        )
        print("CREATED FLOOR PLAN CLONE:", sheet_view.Name, sheet_view.Id.IntegerValue)
        print(
            "SHEET MODE -> source scanned pipe tag count:",
            pre_sheet_tag_count,
        )
        print(
            "SHEET MODE -> clone scanned pipe tag count:",
            count_pipe_tags_for_scanned_pipes(doc, sheet_view, scanned_pipe_ids),
        )

        code_map = {}
        for e_data in result.get("Elements", []):
            if e_data.get("Category") == "Pipes":
                eid = safe_int(e_data.get("Id"), 0)
                if eid > 0:
                    code_map[eid] = e_data.get("NewCode") or ""

        (
            view3d_normal,
            normal_pipe_count,
            normal_fitting_count,
            excluded_nlrs_normal,
            normal_locked,
        ) = create_prefab_3d_normal_view(doc, sheet_code, gathered_elements)
        normal_annot = annotate_prefab_3d_view(
            doc, uidoc, view3d_normal, gathered_elements, code_map
        )
        print(
            "3D NORMAL -> annotation mode:",
            normal_annot.get("mode"),
            "tags={}".format(normal_annot.get("tags")),
            "text={}".format(normal_annot.get("text")),
        )

        (
            view3d_exploded,
            explode_mode,
            exploded_pipe_count,
            exploded_fitting_count,
            exploded_group_count,
            excluded_nlrs_exploded,
            exploded_locked,
        ) = create_prefab_3d_exploded_view(doc, sheet_code, gathered_elements)
        expl_annot = annotate_exploded_3d_with_real_tags(
            doc, view3d_exploded, gathered_elements, code_map
        )
        print(
            "3D EXPLODED -> annotation mode:",
            expl_annot.get("mode"),
            "tags={}".format(expl_annot.get("tags")),
            "text={}".format(expl_annot.get("text")),
        )

        with Transaction(doc, "Create sheet & place plan (safe)") as t3:
            t3.Start()
            sheet = ViewSheet.Create(doc, title_block.Id)
            sheet.SheetNumber = sheet_code
            sheet.Name = "Prefab " + sheet_code

            place_prefab_views_on_sheet(
                doc, sheet, sheet_view, view3d_normal, view3d_exploded
            )
            t3.Commit()
        MessageBox.Show(
            "Prefab text and tags were applied to the current scanned view.\nSheet contains the floor plan clone, 3D normal, and 3D exploded views.\nSource view: {}\nSheet view: {}\n3D normal: {}\n3D exploded: {}\n\nSheet: {}".format(
                target_view.Name,
                sheet_view.Name,
                view3d_normal.Name,
                view3d_exploded.Name,
                sheet.SheetNumber,
            ),
            "Done",
        )
        regular_pipe_tag_count = count_pipe_tags_for_scanned_pipes(
            doc, target_view, scanned_pipe_ids
        )
        extra_horizontal_tag_count = count_specific_pipe_tags_in_view(
            doc, target_view, TAGCFG_EXTRA_H["family"], TAGCFG_EXTRA_H["type"]
        )
        print("FINAL -> floor plan regular pipe tags:", regular_pipe_tag_count)
        print("FINAL -> floor plan extra horizontal tags:", extra_horizontal_tag_count)
        print("FINAL -> floor plan extra tags:", extra_horizontal_tag_count)
        print("FINAL -> 3D normal locked:", normal_locked)
        print("FINAL -> 3D normal real tags:", normal_annot.get("tags", 0))
        print("FINAL -> 3D normal extra tags:", normal_annot.get("extra_real", 0))
        print("FINAL -> 3D exploded locked:", exploded_locked)
        print("FINAL -> 3D exploded real tags:", expl_annot.get("tags", 0))
        print("FINAL -> 3D exploded extra tags:", expl_annot.get("extra_real", 0))
        print(
            "FINAL -> 3D normal vertical no-leader:",
            normal_annot.get("vertical_no_leader", 0),
        )
        print(
            "FINAL -> 3D exploded vertical no-leader:",
            expl_annot.get("vertical_no_leader", 0),
        )
        print(
            "FINAL -> 3D exploded regular leader tags:",
            expl_annot.get("regular_leader", 0),
        )
        print(
            "FINAL -> 3D exploded extra leader tags:",
            expl_annot.get("extra_leader", 0),
        )
        print("FINAL -> 3D normal excluded NLRS pipes:", excluded_nlrs_normal)
        print("FINAL -> 3D exploded excluded NLRS pipes:", excluded_nlrs_exploded)
        print(
            "FINAL -> 3D normal vertical on-host tags:",
            normal_annot.get("vertical_onhost", 0),
        )
        print(
            "FINAL -> 3D exploded vertical on-host tags:",
            expl_annot.get("vertical_onhost", 0),
        )
        for dist_mm in getattr(expl_annot, "get", lambda *a, **k: [])(
            "sample_head_distances", []
        )[:5]:
            print("FINAL -> exploded sample tag distance mm:", dist_mm)
        print("FINAL -> 3D normal filtered pipes:", normal_pipe_count)
        print("FINAL -> 3D normal filtered fittings:", normal_fitting_count)
        print("FINAL -> 3D exploded filtered pipes:", exploded_pipe_count)
        print("FINAL -> 3D exploded filtered fittings:", exploded_fitting_count)
        print("FINAL -> 3D exploded connected groups:", exploded_group_count)
    except Exception as ex:
        TaskDialog.Show(
            "Bunge Sheet Gen",
            "Sheet creation failed, but tags/text are kept.\n\n{}".format(ex),
        )


main()
