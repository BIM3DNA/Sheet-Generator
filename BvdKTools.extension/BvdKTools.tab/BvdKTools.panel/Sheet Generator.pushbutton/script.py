# -*- coding: utf-8 -*-
__title__ = "Sheet\nGenerator"
__doc__ = """Version = 1.0
Date    = 12.04.2025
________________________________________________________________
Description:

This integrated script lets you:
  1. Select boundary detail lines that form a closed loop.
  2. Gather all elements in the active view whose bounding-box center is inside the boundary.
  3. Filter the gathered elements to only those in the categories of interest 
     (Pipes, Pipe Fittings, Pipe Tags, Text Notes).
  4. Display an editable grid so you can adjust the associated prefab (Comments) codes.
  5. Use the Text Note code as the base number.
       â€¢ For Pipes and Pipe Tags, the NewCode becomes: [Base].1, [Base].2, â€¦ (sorted left-to-right, bottom-to-up).
       â€¢ For Pipe Fittings, the NewCode is simply [Base].

If a Text Note is missing from the selected region, you have the option to place one.
________________________________________________________________
How-To:

1. [Hold ALT + CLICK] on the button to open its source folder.
2. Create your boundary (with detail lines) in the view.
3. Click the button and follow the prompts.
________________________________________________________________
Author: Emin Avdovic"""

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
    FormatOptions,
    FilterStringRule,
    FilterStringRuleEvaluator,
    FilterStringBeginsWith,
    FilterStringContains,
    FilterStringEquals,
    XYZ,
    Transaction,
    TextNote,
    TextNoteType,
    TextNoteOptions,
    IndependentTag,
    UV,
    UnitTypeId,
    Reference,
    TagMode,
    TagOrientation,
    ViewSchedule,
    ViewSheet,
    ViewDuplicateOption,
    ViewDiscipline,
    Viewport,
    ParameterValueProvider,
    ParameterFilterElement,
    ScheduleSheetInstance,
    ScheduleFilter,
    ScheduleFilterType,
    ScheduleSortGroupField,
    ScheduleSortOrder,
    StorageType,
    SectionType,
    Category,
)
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.UI import *
from Autodesk.Revit.UI import TaskDialog
from Autodesk.Revit.UI import UIDocument
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
    FormBorderStyle,
    AnchorStyles,
    AutoScaleMode,
    Form,
    ComboBox,
    ListBox,
    PictureBox,
    PictureBoxSizeMode,
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
    ScrollBars,
    Application,
)
from System.Drawing import Image, Point, Color, Rectangle, Size
from System.IO import MemoryStream
from System.Windows.Forms import DataGridViewButtonColumn

from System import Array
import math, re, sys

# ==================================================
# Revit Document Setup
# ==================================================
app = __revit__.Application
uidoc = __revit__.ActiveUIDocument
doc = __revit__.ActiveUIDocument.Document

VERBOSE = False
DEBUG_DIALOGS = False
ANNOTATE_3D = True
ANNOTATE_3D_MODE = "TEXT"
DUPLICATE_ONLY_RELEVANT_SCHEDULES = True
SKIP_EMPTY_SCHEDULES = True


def debug(*args):
    if VERBOSE:
        print(" ".join([str(a) for a in args]))


# ==================================================
# Helper Functions
# ==================================================


def normalize_code(raw):
    if raw is None:
        return ""
    s = str(raw).strip()
    s = s.lower().replace("prefab", "").strip()
    for ch in [":", "-", "_"]:
        if s.startswith(ch):
            s = s[1:].strip()
    s = " ".join(s.split())
    m = re.search(r"(\d+(?:\.\d+)*)", s)
    return m.group(1) if m else ""


def rollback_transaction(tx):
    if not tx:
        return
    try:
        tx.RollBack()
    except Exception:
        pass


class Tx(object):
    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.t = None

    def __enter__(self):
        self.t = Transaction(self.doc, self.name)
        self.t.Start()
        return self.t

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            try:
                if self.t.HasStarted():
                    self.t.RollBack()
            except Exception:
                pass
            return False
        else:
            try:
                self.t.Commit()
            except Exception:
                try:
                    self.t.RollBack()
                except Exception:
                    pass
                raise
            return True


def require_modifiable(doc, where):
    if doc.IsReadOnly:
        raise Exception("DOC READONLY at: {}".format(where))
    if not doc.IsModifiable:
        raise Exception("DOC NOT MODIFIABLE at: {} (no open transaction)".format(where))


def _assert_doc_not_readonly(doc, where="unknown"):
    if doc.IsReadOnly:
        raise Exception("DOC READONLY at: {}".format(where))


def _assert_doc_modifiable(doc, where="unknown"):
    require_modifiable(doc, where)


def set_instance_comment(elem, prefab_code):
    p = elem.LookupParameter("Comments")
    if p and not p.IsReadOnly:
        p.Set(prefab_code)


def get_type_param(elem, name):
    try:
        t = elem.Document.GetElement(elem.GetTypeId())
        if not t:
            return None
        p = t.LookupParameter(name)
        return p.AsString() if p else None
    except Exception:
        return None


def get_inst_param(elem, name):
    try:
        p = elem.LookupParameter(name)
        return p.AsString() if p else None
    except Exception:
        return None


def is_view3d_perspective(v):
    return isinstance(v, View3D) and v.IsPerspective


def _activate_view(uidoc, view):
    """Force Revit to lock ownerDBViewId context for view-owned element creation (tags, detail items, etc.)."""
    try:
        uidoc.ActiveView = view
        return True
    except Exception:
        pass

    try:
        uidoc.RequestViewChange(view)
        return True
    except Exception:
        return False


def ui_yield(ms=150):
    """Let Revit process UI messages so RequestViewChange/ActiveView actually takes effect."""
    try:
        import System
        from System.Windows.Forms import Application

        Application.DoEvents()
        System.Threading.Thread.Sleep(ms)
        Application.DoEvents()
    except Exception:
        pass


def ensure_view_is_active(uidoc, doc, view, retries=5, delay_ms=120):
    try:
        from System.Windows.Forms import Application as WinFormsApplication
    except Exception:
        WinFormsApplication = None

    for _ in range(retries):
        if _activate_view(uidoc, view):
            try:
                if WinFormsApplication:
                    WinFormsApplication.DoEvents()
            except Exception:
                pass
            try:
                doc.Regenerate()
            except Exception:
                pass
            try:
                System.Threading.Thread.Sleep(delay_ms)
            except Exception:
                pass
            try:
                if uidoc.ActiveView and uidoc.ActiveView.Id == view.Id:
                    return True
            except Exception:
                pass
    return False


def get_element_annotation_point(elem, view=None):
    try:
        loc = elem.Location
        if hasattr(loc, "Curve") and loc.Curve:
            return loc.Curve.Evaluate(0.5, True)
        if hasattr(loc, "Point") and loc.Point:
            return loc.Point
    except Exception:
        pass
    try:
        bbox = elem.get_BoundingBox(view) if view else elem.get_BoundingBox(None)
        if bbox:
            return XYZ(
                (bbox.Min.X + bbox.Max.X) / 2.0,
                (bbox.Min.Y + bbox.Max.Y) / 2.0,
                (bbox.Min.Z + bbox.Max.Z) / 2.0,
            )
    except Exception:
        pass
    return XYZ(0, 0, 0)


def get_element_type_name(elem):
    try:
        elem_type = elem.Document.GetElement(elem.GetTypeId())
        if elem_type and hasattr(elem_type, "Name"):
            return elem_type.Name or ""
    except Exception:
        pass
    return ""


def get_element_comment_code(elem):
    return normalize_code(get_inst_param(elem, "Comments") or "")


def ensure_orthographic_3d_view(doc, view3d):
    if not isinstance(view3d, View3D):
        return view3d
    try:
        if not view3d.IsPerspective:
            return view3d
    except Exception:
        return view3d

    vft = None
    for candidate in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        if candidate.ViewFamily == ViewFamily.ThreeDimensional:
            vft = candidate
            break
    if not vft:
        raise Exception("No 3D ViewFamilyType found")

    new3d = View3D.CreateIsometric(doc, vft.Id)
    try:
        new3d.Name = view3d.Name + " - ORTHO"
    except Exception:
        pass
    return new3d


def get_existing_plan_tag_type_ids(doc, source_view):
    tag_type_ids = {}
    tag_category_map = {
        int(BuiltInCategory.OST_PipeTags): "pipe",
    }
    fitting_tag_bic = getattr(BuiltInCategory, "OST_PipeFittingTags", None)
    if fitting_tag_bic is not None:
        tag_category_map[int(fitting_tag_bic)] = "fitting"
    accessory_tag_bic = getattr(BuiltInCategory, "OST_PipeAccessoryTags", None)
    if accessory_tag_bic is not None:
        tag_category_map[int(accessory_tag_bic)] = "accessory"

    for tag in FilteredElementCollector(
        doc, source_view.Id
    ).WhereElementIsNotElementType():
        try:
            if not tag.Category:
                continue
            role = tag_category_map.get(tag.Category.Id.IntegerValue)
            if not role or role in tag_type_ids:
                continue
            tag_type_ids[role] = tag.GetTypeId()
        except Exception:
            continue
    return tag_type_ids


def get_loaded_tag_type_ids(doc, existing_type_ids):
    tag_type_ids = dict(existing_type_ids)
    family_symbols = FilteredElementCollector(doc).OfClass(FamilySymbol).ToElements()
    category_role_map = {
        int(BuiltInCategory.OST_PipeTags): "pipe",
    }
    fitting_tag_bic = getattr(BuiltInCategory, "OST_PipeFittingTags", None)
    if fitting_tag_bic is not None:
        category_role_map[int(fitting_tag_bic)] = "fitting"
    accessory_tag_bic = getattr(BuiltInCategory, "OST_PipeAccessoryTags", None)
    if accessory_tag_bic is not None:
        category_role_map[int(accessory_tag_bic)] = "accessory"

    for symbol in family_symbols:
        try:
            if not symbol.Category:
                continue
            role = category_role_map.get(symbol.Category.Id.IntegerValue)
            if role and role not in tag_type_ids:
                tag_type_ids[role] = symbol.Id
        except Exception:
            continue
    return tag_type_ids


def get_3d_tag_type_ids(doc, source_view):
    return get_loaded_tag_type_ids(
        doc, get_existing_plan_tag_type_ids(doc, source_view)
    )


def get_tag_role_for_element(elem):
    if not elem or not elem.Category:
        return None
    cat_id = elem.Category.Id.IntegerValue
    if cat_id == int(BuiltInCategory.OST_PipeCurves):
        return "pipe"
    # disable 3D tagging for fittings/accessories and all other categories
    return None


def create_3d_text_fallback(doc, view3d, elem, point, prefab_code_str):
    text_type = FilteredElementCollector(doc).OfClass(TextNoteType).FirstElement()
    if not text_type:
        return
    role = get_tag_role_for_element(elem)
    type_name = get_element_type_name(elem)
    if role == "pipe":
        d = (
            get_param_display(elem, "Outside Diameter")
            or get_param_display(elem, "Diameter")
            or "-"
        )
        l = get_param_display(elem, "Length") or "-"
        label = "\u00d8{} / L={} | {}".format(d, l, prefab_code_str)
    elif role == "fitting":
        s = (
            get_param_display(elem, "Size")
            or get_param_display(elem, "Outside Diameter")
            or get_param_display(elem, "Diameter")
            or "-"
        )
        label = "Fitting {} | {}".format(s, prefab_code_str)
    elif role == "accessory":
        desc = get_param_display(elem, "Description") or type_name or "Accessory"
        s = (
            get_param_display(elem, "Size")
            or get_param_display(elem, "Diameter")
            or "-"
        )
        label = "Acc {} {} | {}".format(s, desc or "", prefab_code_str)
    else:
        label = "{} | {}".format(type_name, prefab_code_str)
    opts = TextNoteOptions(text_type.Id)
    TextNote.Create(doc, view3d.Id, point, label, opts)


def annotate_3d_view(doc, view3d, scanned_elems, prefab_code_str, source_view):
    if not ANNOTATE_3D:
        return 0

    def _vec_len(v):
        try:
            return math.sqrt(v.X * v.X + v.Y * v.Y + v.Z * v.Z)
        except Exception:
            return 0.0

    def _norm(v, fallback):
        mag = _vec_len(v)
        if mag < 1e-9:
            return fallback
        return XYZ(v.X / mag, v.Y / mag, v.Z / mag)

    def _unhide_category(bic, label):
        try:
            cat = doc.Settings.Categories.get_Item(bic)
            if cat and view3d.CanCategoryBeHidden(cat.Id):
                view3d.SetCategoryHidden(cat.Id, False)
            print("3D TAGGING -> unhide {}: success".format(label))
            return cat.Id if cat else ElementId(bic)
        except Exception as ex:
            print("3D TAGGING -> unhide {}: failure {}".format(label, ex))
            return ElementId(bic)

    def _is_cat_hidden(cat_id):
        try:
            return bool(view3d.GetCategoryHidden(cat_id))
        except Exception:
            return "n/a"

    def _verify_tag(tag, expected_view_id, tag_cat_id):
        if not tag:
            return False
        try:
            tag_id = tag.Id.IntegerValue
        except Exception:
            tag_id = "n/a"
        print("3D TAG VERIFY -> tag id:", tag_id)
        try:
            print("3D TAG VERIFY -> owner view id:", tag.OwnerViewId.IntegerValue)
        except Exception:
            print("3D TAG VERIFY -> owner view id:", "n/a")
        print("3D TAG VERIFY -> expected view id:", expected_view_id.IntegerValue)
        try:
            print(
                "3D TAG VERIFY -> tag category:",
                tag.Category.Name if tag.Category else "n/a",
            )
        except Exception:
            print("3D TAG VERIFY -> tag category:", "n/a")
        try:
            print(
                "3D TAG VERIFY -> tag type id / symbol id:",
                tag.GetTypeId().IntegerValue,
            )
        except Exception:
            print("3D TAG VERIFY -> tag type id / symbol id:", "n/a")
        try:
            print("3D TAG VERIFY -> tag text:", tag.TagText)
        except Exception:
            print("3D TAG VERIFY -> tag text:", "n/a")
        try:
            print("3D TAG VERIFY -> HasLeader:", tag.HasLeader)
        except Exception:
            print("3D TAG VERIFY -> HasLeader:", "n/a")
        try:
            print("3D TAG VERIFY -> head position:", tag.TagHeadPosition)
        except Exception:
            print("3D TAG VERIFY -> head position:", "n/a")
        bbox_exists = False
        try:
            bbox_exists = tag.get_BoundingBox(view3d) is not None
        except Exception:
            bbox_exists = False
        print("3D TAG VERIFY -> bbox in view exists:", bbox_exists)
        hidden = "n/a"
        try:
            hidden = bool(tag.IsHidden(view3d))
        except Exception:
            pass
        print("3D TAG VERIFY -> hidden in view:", hidden)
        print("3D TAG VERIFY -> pipe tag category hidden:", _is_cat_hidden(tag_cat_id))
        if hidden is True:
            try:
                ids = List[ElementId]([tag.Id])
                view3d.UnhideElements(ids)
                print("3D TAG VERIFY -> can unhide:", True)
            except Exception as ex:
                print("3D TAG VERIFY -> can unhide:", False, ex)
        else:
            print("3D TAG VERIFY -> can unhide:", "n/a")
        visible_candidate = bool(
            bbox_exists
            and hidden is not True
            and _is_cat_hidden(tag_cat_id) is not True
        )
        print("3D TAG VERIFY -> visible candidate:", visible_candidate)
        return bbox_exists

    created_3d = 0
    failed_3d = 0
    skipped_3d = 0
    attempted_3d = 0
    base_code = normalize_code(prefab_code_str)

    print("3D TAGGING -> view id:", view3d.Id.IntegerValue if view3d else "n/a")
    print("3D TAGGING -> view name:", view3d.Name if view3d else "n/a")
    print("3D TAGGING -> is View3D:", isinstance(view3d, View3D))

    tag_cat_id = _unhide_category(BuiltInCategory.OST_PipeTags, "OST_PipeTags")
    _unhide_category(BuiltInCategory.OST_PipeCurves, "OST_PipeCurves")
    _unhide_category(BuiltInCategory.OST_PipeFitting, "OST_PipeFitting")

    locked_before = False
    locked_after = False
    try:
        locked_before = bool(view3d.IsLocked) if hasattr(view3d, "IsLocked") else False
    except Exception:
        locked_before = False
    print("3D TAGGING -> locked before:", locked_before)
    try:
        if (
            isinstance(view3d, View3D)
            and hasattr(view3d, "SaveOrientationAndLock")
            and not locked_before
        ):
            view3d.SaveOrientationAndLock()
    except Exception as ex:
        print("3D TAGGING -> lock failed:", ex)
    try:
        locked_after = (
            bool(view3d.IsLocked) if hasattr(view3d, "IsLocked") else locked_before
        )
    except Exception:
        locked_after = locked_before
    print("3D TAGGING -> locked after:", locked_after)

    up = XYZ(0, 0, 1)
    right = XYZ(1, 0, 0)
    try:
        orientation = view3d.GetOrientation()
        up = _norm(orientation.UpDirection, XYZ(0, 0, 1))
        forward = _norm(orientation.ForwardDirection, XYZ(0, 1, 0))
        try:
            right = forward.CrossProduct(up)
        except Exception:
            right = XYZ(1, 0, 0)
        if _vec_len(right) < 1e-9:
            try:
                right = up.CrossProduct(forward)
            except Exception:
                right = XYZ(1, 0, 0)
        right = _norm(right, XYZ(1, 0, 0))
    except Exception as ex:
        print("3D TAGGING -> orientation read failed:", ex)
    print("3D TAGGING -> right dir:", right)
    print("3D TAGGING -> up dir:", up)

    tag_type_ids = get_3d_tag_type_ids(doc, source_view)
    tag_type_id = tag_type_ids.get("pipe")
    print(
        "3D TAGGING -> tag symbol id:",
        tag_type_id.IntegerValue if tag_type_id else None,
    )

    pipe_candidates = []
    for elem in scanned_elems or []:
        if not elem or not elem.IsValidObject:
            skipped_3d += 1
            print("3D TAGGING -> skipped reason:", "invalid element")
            continue
        if not elem.Category or elem.Category.Id.IntegerValue != int(
            BuiltInCategory.OST_PipeCurves
        ):
            continue
        try:
            loc = elem.Location
            if not (loc and hasattr(loc, "Curve") and loc.Curve):
                skipped_3d += 1
                print(
                    "3D TAGGING -> skipped reason:",
                    "pipe has no LocationCurve",
                    elem.Id.IntegerValue,
                )
                continue
        except Exception:
            skipped_3d += 1
            print(
                "3D TAGGING -> skipped reason:",
                "pipe curve read failed",
                elem.Id.IntegerValue,
            )
            continue
        p = elem.LookupParameter("Comments")
        try:
            raw_comments = p.AsString() or "" if p else ""
        except Exception:
            raw_comments = ""
        norm_comments = normalize_code(raw_comments)
        if not norm_comments or base_code not in norm_comments:
            skipped_3d += 1
            print(
                "3D TAGGING -> skipped reason:",
                "comments do not match base",
                elem.Id.IntegerValue,
                raw_comments,
            )
            continue
        pipe_candidates.append((elem, raw_comments))
        print("3D TAGGING -> candidate pipe id:", elem.Id.IntegerValue)
        print("3D TAGGING -> candidate comments:", raw_comments)

    print("3D TAGGING -> pipe candidates:", len(pipe_candidates))
    if tag_type_id is None:
        print("3D TAGGING -> skipped reason:", "no pipe tag type available")
        print("3D TAGGING -> attempted:", 0)
        print("3D TAGGING -> created:", 0)
        print("3D TAGGING -> failed:", len(pipe_candidates))
        print("3D tags created: 0")
        return 0

    tags_created = []
    for idx, pair in enumerate(pipe_candidates):
        elem, _comments = pair
        attempted_3d += 1
        try:
            curve = elem.Location.Curve
            midpoint = curve.Evaluate(0.5, True)
            stagger = (idx % 6) * 0.35
            tag_point = XYZ(
                midpoint.X + right.X * 0.8 + up.X * stagger,
                midpoint.Y + right.Y * 0.8 + up.Y * stagger,
                midpoint.Z + right.Z * 0.8 + up.Z * stagger,
            )
            print("3D TAGGING -> midpoint:", midpoint)
            print("3D TAGGING -> tag head target:", tag_point)
            tag = IndependentTag.Create(
                doc,
                view3d.Id,
                Reference(elem),
                True,
                TagMode.TM_ADDBY_CATEGORY,
                TagOrientation.Horizontal,
                tag_point,
            )
            try:
                tag.ChangeTypeId(tag_type_id)
            except Exception as ex:
                print(
                    "3D TAGGING -> skipped reason:",
                    "ChangeTypeId failed",
                    elem.Id.IntegerValue,
                    ex,
                )
            try:
                tag.HasLeader = True
                print("3D TAGGING -> set leader: success")
            except Exception as ex:
                print("3D TAGGING -> set leader: failure", ex)
            try:
                tag.TagHeadPosition = tag_point
            except Exception as ex:
                print("3D TAGGING -> set head failed:", ex)
            try:
                ref = Reference(elem)
                if hasattr(tag, "SetLeaderEnd"):
                    tag.SetLeaderEnd(ref, midpoint)
                if hasattr(tag, "SetLeaderElbow"):
                    elbow = XYZ(
                        midpoint.X + (tag_point.X - midpoint.X) * 0.45,
                        midpoint.Y + (tag_point.Y - midpoint.Y) * 0.45,
                        midpoint.Z + (tag_point.Z - midpoint.Z) * 0.45,
                    )
                    tag.SetLeaderElbow(ref, elbow)
            except Exception as ex:
                print("3D TAGGING -> set leader geometry failed:", ex)
            doc.Regenerate()
            _verify_tag(tag, view3d.Id, tag_cat_id)
            tags_created.append(tag.Id)
            created_3d += 1
        except Exception as ex:
            failed_3d += 1
            print(
                "3D tag skipped for element {}: {} ({})".format(
                    elem.Id.IntegerValue, ex, ex.__class__.__name__
                )
            )

    post_tags = []
    post_bbox_count = 0
    post_hidden_count = 0
    try:
        for tag in (
            FilteredElementCollector(doc, view3d.Id)
            .OfClass(IndependentTag)
            .ToElements()
        ):
            try:
                if tag.OwnerViewId != view3d.Id:
                    continue
            except Exception:
                pass
            post_tags.append(tag)
            try:
                if tag.get_BoundingBox(view3d) is not None:
                    post_bbox_count += 1
            except Exception:
                pass
            try:
                if tag.IsHidden(view3d):
                    post_hidden_count += 1
            except Exception:
                pass
    except Exception as ex:
        print("3D TAGGING POST -> collect failed:", ex)
    print("3D TAGGING POST -> tags in target 3D view:", len(post_tags))
    print("3D TAGGING POST -> tag ids:", [t.Id.IntegerValue for t in post_tags])
    print("3D TAGGING POST -> tags with bbox:", post_bbox_count)
    print("3D TAGGING POST -> tags hidden count:", post_hidden_count)
    if created_3d > 0 and post_bbox_count == 0:
        print(
            "3D TAGGING WARNING -> tags created but not visible in view; likely unsupported tag family/category visibility/crop issue."
        )

    print("3D TAGGING -> attempted:", attempted_3d)
    print("3D TAGGING -> created:", created_3d)
    print("3D TAGGING -> failed:", failed_3d)
    print("3D TAGGING -> skipped reason:", "total skipped {}".format(skipped_3d))
    print("3D tags created: {}".format(created_3d))
    return created_3d


def safe_duplicate_view(doc, view, opt, label="About to duplicate view"):
    primary_view_id = "n/a"
    if hasattr(view, "PrimaryViewId"):
        try:
            primary_view_id = view.PrimaryViewId.IntegerValue
        except Exception:
            primary_view_id = "n/a"
    is_temp = False
    try:
        is_temp = view.IsTemplate
    except Exception:
        pass
    msg = "DUPLICATE VIEW REQUEST: name='{}' id={} type={} isTemplate={}".format(
        view.Name, view.Id.IntegerValue, view.ViewType, is_temp
    )
    if isinstance(view, View3D):
        try:
            msg += " isPerspective={}".format(view.IsPerspective)
        except Exception:
            pass
    print(msg)
    if DEBUG_DIALOGS:
        TaskDialog.Show(
            "SheetGen Debug",
            "{}\nId: {}\nName: {}\nViewType: {}\nIsTemplate: {}\nPrimaryViewId: {}".format(
                label,
                view.Id.IntegerValue,
                view.Name,
                view.ViewType,
                is_temp,
                primary_view_id,
            ),
        )
    if is_temp:
        raise Exception("Refusing to duplicate a template view: {}".format(view.Name))

    if is_view3d_perspective(view):
        vft = None
        for x in FilteredElementCollector(doc).OfClass(ViewFamilyType):
            if x.ViewFamily == ViewFamily.ThreeDimensional:
                vft = x
                break
        if not vft:
            raise Exception("No 3D ViewFamilyType found")
        new3d = View3D.CreateIsometric(doc, vft.Id)
        new3d.Name = view.Name + " - ISO"
        return new3d.Id

    can = True
    try:
        can = view.CanViewBeDuplicated(opt)
    except Exception:
        pass
    if not can:
        raise Exception(
            "View cannot be duplicated (CanViewBeDuplicated=False): {}".format(
                view.Name
            )
        )

    require_modifiable(doc, "safe_duplicate_view.Duplicate")
    return view.Duplicate(opt)


def resolve_dup_source_plan_view(doc, candidate_view, preferred_name_contains=None):
    def get_primary_view(view):
        if view is None:
            return None
        if hasattr(view, "GetPrimaryViewId"):
            try:
                pid = view.GetPrimaryViewId()
                if pid and pid != ElementId.InvalidElementId:
                    view = doc.GetElement(pid)
            except Exception:
                pass
        return view

    def is_primary_view_plan(view):
        if not isinstance(view, ViewPlan):
            return False
        if view.IsTemplate:
            return False
        if hasattr(view, "GetPrimaryViewId"):
            try:
                return view.GetPrimaryViewId() == ElementId.InvalidElementId
            except Exception:
                return True
        return True

    candidate = get_primary_view(candidate_view)
    if is_primary_view_plan(candidate):
        return candidate

    all_view_plans = list(FilteredElementCollector(doc).OfClass(ViewPlan).ToElements())

    if preferred_name_contains:
        preferred_name_contains_lc = preferred_name_contains.lower()
        for v in all_view_plans:
            if preferred_name_contains_lc not in v.Name.lower():
                continue
            if is_primary_view_plan(v):
                return v

    for v in all_view_plans:
        if is_primary_view_plan(v):
            return v

    return None


def duplicate_or_recreate_plan_view(
    doc, source_view, dup_option_preferred, desired_name
):
    source_name = source_view.Name if source_view else "<None>"
    if not isinstance(source_view, ViewPlan) or source_view.IsTemplate:
        raise Exception(
            "Invalid source view for plan duplication: {}".format(source_name)
        )

    options = [
        ViewDuplicateOption.WithDetailing,
        ViewDuplicateOption.Duplicate,
        ViewDuplicateOption.AsDependent,
    ]
    if dup_option_preferred in options:
        options = [dup_option_preferred] + [
            o for o in options if o != dup_option_preferred
        ]

    for opt in options:
        try:
            new_id = safe_duplicate_view(
                doc,
                source_view,
                opt,
                "About to duplicate: plan source ({})".format(opt),
            )
            v = doc.GetElement(new_id)
            try:
                v.Name = desired_name
            except Exception:
                pass
            return v, opt
        except InvalidOperationException:
            continue

    vfts = FilteredElementCollector(doc).OfClass(ViewFamilyType).ToElements()
    floor_vft = next(vft for vft in vfts if vft.ViewFamily == ViewFamily.FloorPlan)

    level_id = source_view.GenLevel.Id
    new_plan = ViewPlan.Create(doc, floor_vft.Id, level_id)
    try:
        new_plan.Name = desired_name
    except Exception:
        pass

    new_plan.Scale = source_view.Scale
    new_plan.Discipline = source_view.Discipline

    ids_to_copy = List[ElementId]()
    for el in (
        FilteredElementCollector(doc, source_view.Id)
        .WhereElementIsNotElementType()
        .ToElements()
    ):
        if el.Category and el.Category.CategoryType == CategoryType.Annotation:
            ids_to_copy.Add(el.Id)

    if ids_to_copy.Count > 0:
        ElementTransformUtils.CopyElements(
            source_view,
            ids_to_copy,
            new_plan,
            Transform.Identity,
            CopyPasteOptions(),
        )

    return new_plan, "Created"


SCHEDULE_TEMPLATES = {
    "GEBERIT_PE_PIPES": [
        "Geberit PE leidingen",
        "Geberit PE leidingen - PREFAB -1",
        "Geberit PE leiding",
        "Geberit PE pipes",
    ],
    "GEBERIT_PE_FITTINGS": [
        "Geberit PE fittingen",
        "Geberit PE fittingen - PREFAB -1",
        "Geberit PE fitting",
        "Geberit PE fittings",
    ],
    "GEBERIT_SILENT_PIPES": ["Geberit PE Silent leidingen - PREFAB -1"],
    "GEBERIT_SILENT_FITTINGS": ["Geberit PE Silent fittingen - PREFAB -1"],
    "WAVIN_TIGRIS_PIPES": ["Wavin Tigris Pipe Schedule -1"],
    "WAVIN_TIGRIS_FITTINGS": ["Wavin Tigris Pipe Fitting Schedule"],
    "WAVIN_TIGRIS_FLEX": ["Wavin Tigris Flexible Pipe Schedule -2"],
    "WAVIN_WAFIX_PP_PIPES": ["Wavin Wafix PP Pipes"],
    "WAVIN_WAFIX_PP_FITTINGS": ["Wavin Wafix PP Pipe Fittings BOM -1"],
    "PIPE_ACCESSORIES": ["Pipe Accessory Schedule - PREFAB -1"],
    "PLUMBING_FIXTURES": ["Plumbing Fixture Schedule - PREFAB -2"],
}


def normalize_schedule_name(name):
    return " ".join(name.lower().replace("_", " ").replace("-", " ").split())


def find_schedule_view_by_name(doc, name):
    for vs in FilteredElementCollector(doc).OfClass(ViewSchedule):
        if vs.Name == name:
            return vs
    target = normalize_schedule_name(name)
    for vs in FilteredElementCollector(doc).OfClass(ViewSchedule):
        if normalize_schedule_name(vs.Name) == target:
            return vs
    return None


def _looks_generated_schedule_name(name):
    s = normalize_schedule_name(name)
    if re.search(r"\d+(?:\.\d+)+", s):
        return True
    if re.search(r"prefab\s+\d", s):
        return True
    return False


def find_schedule_template_for_system(doc, syskey, candidate_names):
    print("SCHEDULE TEMPLATE SEARCH -> syskey:", syskey)
    print("SCHEDULE TEMPLATE SEARCH -> candidates:", candidate_names)
    schedules = list(FilteredElementCollector(doc).OfClass(ViewSchedule).ToElements())
    candidate_norms = [normalize_schedule_name(n) for n in candidate_names]
    matches = []
    for vs in schedules:
        try:
            nm = vs.Name or ""
            nm_norm = normalize_schedule_name(nm)
            score = None
            if nm in candidate_names:
                score = 0
            elif nm_norm in candidate_norms:
                score = 1
            else:
                for cn in candidate_norms:
                    tokens = [t for t in cn.split() if t not in ("prefab", "1")]
                    important = [
                        t
                        for t in tokens
                        if t
                        in (
                            "geberit",
                            "pe",
                            "fittingen",
                            "leidingen",
                            "fitting",
                            "fittings",
                            "leiding",
                            "pipes",
                        )
                    ]
                    if important and all(t in nm_norm for t in important):
                        score = 2
                        break
            if score is None:
                continue
            generated = _looks_generated_schedule_name(nm)
            if generated:
                print("SCHEDULE TEMPLATE SEARCH -> rejected generated copy:", nm)
            matches.append((score + (10 if generated else 0), nm.lower(), nm, vs))
        except Exception:
            continue
    print(
        "SCHEDULE TEMPLATE SEARCH -> available matching schedules:",
        [m[2] for m in matches],
    )
    if not matches:
        print("SCHEDULE TEMPLATE SEARCH -> selected:", None)
        return None
    matches.sort(key=lambda x: (x[0], x[1]))
    selected = matches[0][3]
    print("SCHEDULE TEMPLATE SEARCH -> selected:", selected.Name)
    return selected


def get_param_str(elem, name):
    return get_inst_param(elem, name) or get_type_param(elem, name) or ""


def get_param_display(elem, param_name, fallback=""):
    """Return human-readable parameter value. Works for doubles using AsValueString()."""
    if not elem:
        return fallback
    p = elem.LookupParameter(param_name)
    if not p:
        try:
            t = elem.Document.GetElement(elem.GetTypeId())
            if t:
                p = t.LookupParameter(param_name)
        except Exception:
            p = None

    if not p:
        return fallback

    try:
        s = p.AsValueString()
        if s:
            return s
    except Exception:
        pass

    try:
        if p.StorageType == StorageType.Double:
            mm = p.AsDouble() * 304.8
            return "{} mm".format(int(round(mm)))
        if p.StorageType == StorageType.Integer:
            return str(p.AsInteger())
        if p.StorageType == StorageType.String:
            return p.AsString() or fallback
    except Exception:
        pass

    return fallback


def detect_systems(doc, element_ids):
    detected = set()
    for item in element_ids:
        elem = doc.GetElement(item) if isinstance(item, ElementId) else item
        if not elem or not elem.Category:
            continue

        cat_id = elem.Category.Id.IntegerValue
        manufacturer = get_param_str(elem, "Manufacturer").strip().lower()
        product_series = get_param_str(elem, "product_series").strip().lower()
        description = get_param_str(elem, "Description").strip().lower()
        model = get_param_str(elem, "Model").strip().lower()
        signals = " ".join([product_series, description, model])

        if cat_id == int(BuiltInCategory.OST_PipeCurves):
            if "wavin" in manufacturer and "tigris" in product_series:
                detected.add("WAVIN_TIGRIS_PIPES")
            if "wavin" in manufacturer and (
                "wafix" in product_series or "wafix pp" in product_series
            ):
                detected.add("WAVIN_WAFIX_PP_PIPES")
            if "geberit" in manufacturer and ("silent" in signals or "db20" in signals):
                detected.add("GEBERIT_SILENT_PIPES")
            elif "geberit" in manufacturer and "pe" in signals:
                detected.add("GEBERIT_PE_PIPES")

        elif cat_id == int(BuiltInCategory.OST_PipeFitting):
            if "wavin" in manufacturer and "tigris" in product_series:
                detected.add("WAVIN_TIGRIS_FITTINGS")
            if "wavin" in manufacturer and (
                "wafix" in product_series or "wafix pp" in product_series
            ):
                detected.add("WAVIN_WAFIX_PP_FITTINGS")
            if "geberit" in manufacturer and ("silent" in signals or "db20" in signals):
                detected.add("GEBERIT_SILENT_FITTINGS")
            elif "geberit" in manufacturer and "pe" in signals:
                detected.add("GEBERIT_PE_FITTINGS")

        elif cat_id == int(BuiltInCategory.OST_FlexPipeCurves):
            if "wavin" in manufacturer and "tigris" in product_series:
                detected.add("WAVIN_TIGRIS_FLEX")

        elif cat_id == int(BuiltInCategory.OST_PipeAccessory):
            detected.add("PIPE_ACCESSORIES")

        elif cat_id == int(BuiltInCategory.OST_PlumbingFixtures):
            detected.add("PLUMBING_FIXTURES")

    return detected


def apply_prefab_filter_to_schedule(vs, prefab_code):
    try:
        sd = vs.Definition
    except Exception as ex:
        print("Cannot access schedule definition '{}': {}".format(vs.Name, ex))
        return

    print("SCHEDULE FILTER -> schedule:", vs.Name)
    comments_param = ElementId(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    comment_field = None
    for f_id in sd.GetFieldOrder():
        sf = sd.GetField(f_id)
        if sf.ParameterId == comments_param:
            comment_field = sf
            break

    if comment_field is None:
        try:
            cm_sched_field = next(
                f for f in sd.GetSchedulableFields() if f.ParameterId == comments_param
            )
            comment_field = sd.AddField(cm_sched_field)
        except Exception as ex:
            print("Comments field missing for schedule '{}': {}".format(vs.Name, ex))
            return

    comment_field_id = comment_field.FieldId
    print("SCHEDULE FILTER -> Comments field found/created:", True)
    removed_comments_filters = 0
    preserved_non_comments_filters = 0
    for i in reversed(range(sd.GetFilterCount())):
        f = sd.GetFilter(i)
        if f.FieldId == comment_field_id:
            sd.RemoveFilter(i)
            removed_comments_filters += 1
        else:
            preserved_non_comments_filters += 1
    print("SCHEDULE FILTER -> removed old Comments filters:", removed_comments_filters)
    print(
        "SCHEDULE FILTER -> preserved non-Comments filters count:",
        preserved_non_comments_filters,
    )

    filter_type = None
    filter_label = None
    try:
        filter_type = ScheduleFilterType.Contains
        filter_label = "Contains"
    except Exception:
        try:
            filter_type = ScheduleFilterType.BeginsWith
            filter_label = "BeginsWith"
        except Exception:
            filter_type = ScheduleFilterType.Equal
            filter_label = "Equal"
    sd.AddFilter(ScheduleFilter(comment_field_id, filter_type, prefab_code))
    print("SCHEDULE FILTER -> method used:", filter_label)
    print("SCHEDULE FILTER -> value:", prefab_code)
    print(
        "SCHEDULE FILTER -> added Comments contains:",
        "{} ({})".format(prefab_code, filter_label),
    )


def get_schedule_body_row_count(vs):
    try:
        table_data = vs.GetTableData()
        body = table_data.GetSectionData(SectionType.Body)
        return body.NumberOfRows
    except Exception as ex:
        print("Schedule row count failed for '{}': {}".format(vs.Name, ex))
        return None


def duplicate_schedules_for_systems(doc, detected_systems, prefab_code):
    created = []
    processed_sources = set()
    for syskey in sorted(detected_systems):
        template_names = SCHEDULE_TEMPLATES.get(syskey)
        if not template_names:
            print("No schedule template mapping for {}".format(syskey))
            continue
        src = find_schedule_template_for_system(doc, syskey, template_names)
        if not src:
            print(
                "Schedule template not found for {}: {}".format(syskey, template_names)
            )
            continue
        if src.Id.IntegerValue in processed_sources:
            continue
        processed_sources.add(src.Id.IntegerValue)
        sched_name = src.Name

        try:
            require_modifiable(doc, "duplicate_schedules_for_systems.start")
            print("About to duplicate schedule: {}".format(sched_name))
            print("SCHEDULE DUPLICATE -> source:", src.Name)
            try:
                print(
                    "SCHEDULE DUPLICATE -> source IsItemized:",
                    src.Definition.IsItemized,
                )
            except Exception:
                print("SCHEDULE DUPLICATE -> source IsItemized:", "n/a")
            require_modifiable(doc, "duplicate_schedules_for_systems.Duplicate")
            new_id = src.Duplicate(ViewDuplicateOption.Duplicate)
            new_vs = doc.GetElement(new_id)
            target_name = "{} - PREFAB - {}".format(sched_name, prefab_code)
            final_name = target_name
            suffix = 2
            while find_schedule_view_by_name(doc, final_name):
                final_name = "{} ({})".format(target_name, suffix)
                suffix += 1
            require_modifiable(doc, "duplicate_schedules_for_systems.Name")
            new_vs.Name = final_name
            print("SCHEDULE DUPLICATE -> new:", new_vs.Name)
            try:
                print(
                    "SCHEDULE DUPLICATE -> new IsItemized:",
                    new_vs.Definition.IsItemized,
                )
            except Exception:
                print("SCHEDULE DUPLICATE -> new IsItemized:", "n/a")
            if syskey == "GEBERIT_PE_PIPES":
                try:
                    print(
                        "SCHEDULE PIPE ITEMIZATION -> source IsItemized:",
                        src.Definition.IsItemized,
                    )
                except Exception:
                    print("SCHEDULE PIPE ITEMIZATION -> source IsItemized:", "n/a")
                try:
                    print(
                        "SCHEDULE PIPE ITEMIZATION -> new before:",
                        new_vs.Definition.IsItemized,
                    )
                except Exception:
                    print("SCHEDULE PIPE ITEMIZATION -> new before:", "n/a")
                forced_itemized = False
                try:
                    new_vs.Definition.IsItemized = True
                    forced_itemized = True
                except Exception as ex:
                    print("SCHEDULE PIPE ITEMIZATION -> fallback sort by Comments")
                    print("SCHEDULE PIPE ITEMIZATION -> set failed:", ex)
                print(
                    "SCHEDULE PIPE ITEMIZATION -> forced IsItemized True:",
                    forced_itemized,
                )
                try:
                    print(
                        "SCHEDULE PIPE ITEMIZATION -> new after:",
                        new_vs.Definition.IsItemized,
                    )
                except Exception:
                    print("SCHEDULE PIPE ITEMIZATION -> new after:", "n/a")
            apply_prefab_filter_to_schedule(new_vs, prefab_code)

            if SKIP_EMPTY_SCHEDULES:
                doc.Regenerate()
                row_count = get_schedule_body_row_count(new_vs)
                if row_count == 0:
                    require_modifiable(
                        doc, "duplicate_schedules_for_systems.DeleteEmpty"
                    )
                    doc.Delete(new_vs.Id)
                    print("Skipped empty schedule: '{}'".format(final_name))
                    continue

            created.append(new_vs.Id)
        except Exception as ex:
            print("Schedule duplication failed for '{}': {}".format(sched_name, ex))
            continue
    return created


# --- Boundary Selection Functions ---
class DetailLineSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        if elem.Category and elem.Category.Id.IntegerValue == int(
            BuiltInCategory.OST_Lines
        ):
            return True
        return False

    def AllowReference(self, ref, point):
        return False


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
    else:
        return None


def is_point_inside_polygon(point, polygon):
    x = point.X
    y = point.Y
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi = polygon[i].X
        yi = polygon[i].Y
        xj = polygon[j].X
        yj = polygon[j].Y
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        ):
            inside = not inside
        j = i
    return inside


def select_boundary_and_gather():
    try:
        selection_refs = uidoc.Selection.PickObjects(
            ObjectType.Element,
            DetailLineSelectionFilter(),
            "Select boundary detail lines (click on the lines that form a closed loop)",
        )
    except Exception:
        return None
    if not selection_refs:
        return None

    segments = []
    for ref in selection_refs:
        elem = doc.GetElement(ref)
        try:
            curve = elem.GeometryCurve
            start = curve.GetEndPoint(0)
            end = curve.GetEndPoint(1)
            segments.append((start, end))
        except Exception:
            continue

    polygon = order_segments_to_polygon(segments[:])
    if polygon is None:
        MessageBox.Show(
            "The selected detail lines do not form a closed boundary.", "Error"
        )
        return None

    collector = (
        FilteredElementCollector(doc, uidoc.ActiveView.Id)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    elements_inside = []
    for elem in collector:
        bbox = elem.get_BoundingBox(uidoc.ActiveView)
        if bbox:
            center = XYZ(
                (bbox.Min.X + bbox.Max.X) / 2.0,
                (bbox.Min.Y + bbox.Max.Y) / 2.0,
                (bbox.Min.Z + bbox.Max.Z) / 2.0,
            )
            if is_point_inside_polygon(center, polygon):
                elements_inside.append(elem)

    MessageBox.Show(
        "Found {0} element(s) inside the selected boundary.".format(
            len(elements_inside)
        ),
        "Boundary Selection",
    )
    return elements_inside


# --- Parameter and Region Helpers ---
def convert_param_to_string(param_obj):
    if not param_obj:
        return ""
    try:
        val_str = param_obj.AsValueString()
        if val_str and val_str.strip() != "":
            return val_str
    except Exception:
        pass
    try:
        val_double = param_obj.AsDouble()
        val_mm = val_double * 304.8
        return str(int(round(val_mm))) + " mm"
    except Exception:
        return ""


def get_region_bounding_box(elements):
    valid_found = False
    overall_min_x = float("inf")
    overall_min_y = float("inf")
    overall_min_z = float("inf")
    overall_max_x = float("-inf")
    overall_max_y = float("-inf")
    overall_max_z = float("-inf")

    for el in elements:
        try:
            bbox = el.get_BoundingBox(uidoc.ActiveView)
        except:
            continue  # skip if element was just deleted
        if not bbox:
            continue
        if bbox is None:
            continue
        if (
            bbox.Min.X == float("inf")
            or bbox.Min.Y == float("inf")
            or bbox.Min.Z == float("inf")
        ):
            continue
        valid_found = True
        overall_min_x = min(overall_min_x, bbox.Min.X)
        overall_min_y = min(overall_min_y, bbox.Min.Y)
        overall_min_z = min(overall_min_z, bbox.Min.Z)
        overall_max_x = max(overall_max_x, bbox.Max.X)
        overall_max_y = max(overall_max_y, bbox.Max.Y)
        overall_max_z = max(overall_max_z, bbox.Max.Z)

    if not valid_found:
        return XYZ(0, 0, 0), XYZ(0, 0, 0)

    overall_min = XYZ(overall_min_x, overall_min_y, overall_min_z)
    overall_max = XYZ(overall_max_x, overall_max_y, overall_max_z)
    return overall_min, overall_max


def create_pipe_tags_for_untagged_pipes(doc, pipes, view):
    t = Transaction(doc, "Add Missing Pipe Tags")
    t.Start()
    for pipe in pipes:
        bbox = pipe.get_BoundingBox(view)
        if not bbox:
            continue
        center = XYZ(
            (bbox.Min.X + bbox.Max.X) / 2.0,
            (bbox.Min.Y + bbox.Max.Y) / 2.0,
            (bbox.Min.Z + bbox.Max.Z) / 2.0,
        )
        pipe_ref = Reference(pipe)
        IndependentTag.Create(
            doc,
            view.Id,
            pipe_ref,
            True,
            TagMode.TM_ADDBY_CATEGORY,
            TagOrientation.Horizontal,
            UV(center.X, center.Y),
        )
    t.Commit()


# ==================================================
# UI Class: ElementEditorForm
# ==================================================
class ElementEditorForm(Form):
    def __init__(self, elements_data, region_elements=None):
        self.Text = "Edit Element Codes"
        self.Width = 1050
        self.Height = 500
        self.MinimumSize = Size(700, 400)
        self.SuspendLayout()
        self.regionElements = region_elements

        # --- 1. Bottom Buttons Panel FIRST
        self.buttonPanel = System.Windows.Forms.Panel()
        self.buttonPanel.Height = 50
        self.buttonPanel.Dock = DockStyle.Bottom
        self.Controls.Add(self.buttonPanel)

        # --- 2. Panel for the DataGridView SECOND
        self.gridPanel = System.Windows.Forms.Panel()
        self.gridPanel.Dock = DockStyle.Fill
        self.Controls.Add(self.gridPanel)

        self.dataGrid = DataGridView()
        self.dataGrid.SelectionChanged += self.on_row_selected
        self.dataGrid.Dock = DockStyle.Fill
        self.dataGrid.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill
        self.dataGrid.CellContentClick += self.dataGrid_CellContentClick
        self.dataGrid.MultiSelect = True
        self.dataGrid.SelectionMode = DataGridViewSelectionMode.FullRowSelect
        self.gridPanel.Controls.Add(self.dataGrid)

        # --- 3. Columns
        self.colId = DataGridViewTextBoxColumn()
        self.colId.Name = "Id"
        self.colId.HeaderText = "Element Id"
        self.colId.ReadOnly = True

        self.colCategory = DataGridViewTextBoxColumn()
        self.colCategory.Name = "Category"
        self.colCategory.HeaderText = "Category"
        self.colCategory.ReadOnly = True

        self.category_headers = {}
        self.collapsed_categories = set()

        self.colName = DataGridViewTextBoxColumn()
        self.colName.Name = "Name"
        self.colName.HeaderText = "Name"
        self.colName.ReadOnly = True

        self.colWarning = DataGridViewTextBoxColumn()
        self.colWarning.Name = "Warning"
        self.colWarning.HeaderText = "Warning"
        self.colWarning.ReadOnly = True

        self.colBend45 = DataGridViewTextBoxColumn()
        self.colBend45.Name = "Bend45"
        self.colBend45.HeaderText = "2x45Â°"
        self.colBend45.ReadOnly = True

        self.colDefaultCode = DataGridViewTextBoxColumn()
        self.colDefaultCode.Name = "DefaultCode"
        self.colDefaultCode.HeaderText = "Default Code"
        self.colDefaultCode.ReadOnly = True

        self.colNewCode = DataGridViewTextBoxColumn()
        self.colNewCode.Name = "NewCode"
        self.colNewCode.HeaderText = "New Code"
        self.colNewCode.ReadOnly = False

        self.colOD = DataGridViewTextBoxColumn()
        self.colOD.Name = "OutsideDiameter"
        self.colOD.HeaderText = "Outside Diameter"
        self.colOD.ReadOnly = True

        self.colLength = DataGridViewTextBoxColumn()
        self.colLength.Name = "Length"
        self.colLength.HeaderText = "Length"
        self.colLength.ReadOnly = True

        self.colSize = DataGridViewTextBoxColumn()
        self.colSize.Name = "Size"
        self.colSize.HeaderText = "Size"
        self.colSize.ReadOnly = True

        self.colArticle = DataGridViewTextBoxColumn()
        self.colArticle.Name = "GEB_Article_Number"
        self.colArticle.HeaderText = "GEB Article No."
        self.colArticle.ReadOnly = True

        self.colTagStatus = DataGridViewButtonColumn()
        self.colTagStatus.Name = "TagStatus"
        self.colTagStatus.HeaderText = "Tags"
        self.colTagStatus.UseColumnTextForButtonValue = False

        self.dataGrid.Columns.AddRange(
            Array[DataGridViewTextBoxColumn](
                [
                    self.colId,
                    self.colCategory,
                    self.colName,
                    self.colWarning,
                    self.colBend45,
                    self.colDefaultCode,
                    self.colNewCode,
                    self.colOD,
                    self.colLength,
                    self.colSize,
                ]
            )
        )
        self.dataGrid.Columns.Add(self.colArticle)
        self.dataGrid.Columns.Add(self.colTagStatus)

        # 1. Text Note Code Input with Example Text
        self.txtTextNoteCode = TextBox()
        self.txtTextNoteCode.Width = 150
        self.txtTextNoteCode.ForeColor = Color.Gray
        self.txtTextNoteCode.Text = "prefab 5.5.5"
        self.txtTextNoteCode.GotFocus += self.clear_placeholder
        self.txtTextNoteCode.LostFocus += self.restore_placeholder
        self.buttonPanel.Controls.Add(self.txtTextNoteCode)

        # --- 4. Buttons inside buttonPanel
        self.btnPlaceTextNote = Button()
        self.btnPlaceTextNote.Text = "Place Text Note"
        self.btnPlaceTextNote.Width = 150
        self.btnPlaceTextNote.Click += self.btnPlaceTextNote_Click
        self.buttonPanel.Controls.Add(self.btnPlaceTextNote)

        self.btnFixReducers = Button()
        self.btnFixReducers.Text = "Fix Reducers"
        self.btnFixReducers.Width = 150
        self.btnFixReducers.Click += self.btnFixReducers_Click
        self.buttonPanel.Controls.Add(self.btnFixReducers)

        self.btnBulkTags = Button()
        self.btnBulkTags.Text = "Add/Remove Tags"
        self.btnBulkTags.Width = 150
        self.btnBulkTags.Click += self.bulkAddRemoveTags_Click
        self.buttonPanel.Controls.Add(self.btnBulkTags)

        self.btnAutoFill = Button()
        self.btnAutoFill.Text = "Auto-Fill Tag Codes"
        self.btnAutoFill.Width = 150
        self.btnAutoFill.Click += self.autoFillPipeTagCodes
        self.buttonPanel.Controls.Add(self.btnAutoFill)

        self.btnOK = Button()
        self.btnOK.Text = "OK"
        self.btnOK.Width = 80
        self.btnOK.DialogResult = DialogResult.OK
        self.btnOK.Click += self.okButton_Click
        self.buttonPanel.Controls.Add(self.btnOK)

        self.btnCancel = Button()
        self.btnCancel.Text = "Cancel"
        self.btnCancel.Width = 80
        self.btnCancel.DialogResult = DialogResult.Cancel
        self.buttonPanel.Controls.Add(self.btnCancel)

        # --- 5. Smart Button Alignment
        self.buttonPanel.Resize += self.rearrange_buttons

        self.ResumeLayout(False)
        self.PerformLayout()
        self.buttonPanel.PerformLayout()
        self.gridPanel.PerformLayout()
        self.dataGrid.PerformLayout()

        # --- 6. State
        self.textNotePlaced = False
        self.Result = None

        # --- 7. Populate Rows
        for ed in elements_data:
            row_idx = self.dataGrid.Rows.Add()
            row = self.dataGrid.Rows[row_idx]
            row.Cells["Id"].Value = ed["Id"]
            row.Cells["Category"].Value = ed["Category"]
            row.Cells["Name"].Value = ed["Name"]
            row.Cells["Warning"].Value = ed.get("Warning", "")
            row.Cells["Bend45"].Value = ed.get("Bend45", "")
            row.Cells["GEB_Article_Number"].Value = ed.get("GEB_Article_Number", "")
            row.Cells["DefaultCode"].Value = ed["DefaultCode"]
            row.Cells["NewCode"].Value = ed["NewCode"]
            row.Cells["OutsideDiameter"].Value = ed["OutsideDiameter"]
            row.Cells["Length"].Value = ed["Length"]
            row.Cells["Size"].Value = ed.get("Size", "")

            # TagStatus logic
            cat = ed["Category"]
            name = ed["Name"]
            name_lc = name.lower()
            warning_val = row.Cells["Warning"].Value or ""
            debug(">> Pipe Fitting Name:", name)

            if cat == "Pipes":
                if ed["TagStatus"] == "Yes":
                    row.Cells["TagStatus"].Value = "Remove Tag"
                else:
                    row.Cells["TagStatus"].Value = "Add/Place Tag"
                row.DefaultCellStyle.BackColor = Color.LightBlue

            elif cat == "Pipe Tags":
                row.Cells["TagStatus"].Value = "Remove Tag"
                row.DefaultCellStyle.BackColor = Color.LightGreen

            elif cat == "Text Notes":
                row.Cells["TagStatus"].Value = ""
                row.DefaultCellStyle.BackColor = Color.LightGray

            elif cat == "Pipe Fittings":
                elem = doc.GetElement(ElementId(int(ed["Id"])))
                family_name = ""
                if isinstance(elem, FamilyInstance):
                    symbol = elem.Symbol
                    if symbol and symbol.Family:
                        family_name = symbol.Family.Name.lower()

                row.Cells["TagStatus"].Value = ""
                row.Cells["TagStatus"].ReadOnly = True

                if "var. dn/od" in name_lc:
                    if "multibocht" in name_lc or "multibocht" in family_name:
                        row.Cells["TagStatus"].Value = "Flip 2x45Â°"
                        row.Cells["TagStatus"].ReadOnly = False
                    elif "liggend" in name_lc or "liggend" in family_name:
                        row.Cells["TagStatus"].Value = "Flip T-stuk"
                        row.Cells["TagStatus"].ReadOnly = False
                        # row.DefaultCellStyle.BackColor = Color.LightGoldenrodYellow
                    elif "multireducer" in name_lc or "multireducer_geb" in family_name:
                        row.Cells["TagStatus"].Value = "Flip Reducer"
                        row.Cells["TagStatus"].ReadOnly = False

                row.DefaultCellStyle.BackColor = Color.LightGoldenrodYellow

    def auto_fix_inline(self):
        updated = 0
        skipped = 0

        for row in self.dataGrid.Rows:
            try:
                cat = row.Cells["Category"].Value
                if cat != "Pipe Fittings":
                    continue

                eid = int(str(row.Cells["Id"].Value))
                elem = doc.GetElement(ElementId(eid))
                if not elem or not elem.IsValidObject:
                    continue

                name = elem.Name
                debug("Checking:", elem.Id, "| Name:", name)

                # 1. Fix concentric reducers
                reducer_fixed = False
                p_warn = elem.LookupParameter("waarschuwing")
                warning = p_warn.AsString() if p_warn else ""
                debug(" -> Warning:", warning)

                has_concentric_warning = warning and "concentric" in warning.lower()

                has_reducer_params = any(
                    elem.LookupParameter(pn)
                    for pn in [
                        "kort_verloop (kleinste)",
                        "kort_verloop (grootste)",
                        "reducer_eccentric",
                        "switch_excentriciteit",
                    ]
                )
                if has_concentric_warning and has_reducer_params:
                    param_map = {
                        "kort_verloop (kleinste)": True,
                        "kort_verloop (grootste)": True,
                        "reducer_eccentric": True,
                        "switch_excentriciteit": False,
                    }
                    t = Transaction(doc, "Fix Reducer")
                    t.Start()
                    for pname, value in param_map.items():
                        p = elem.LookupParameter(pname)
                        if p and p.StorageType == StorageType.Integer:
                            p.Set(1 if value else 0)
                    t.Commit()
                    debug(" -> Reducer fixed.")
                    updated += 1
                    reducer_fixed = True

                # 2. Turn OFF 2x45Â°
                p_bend = elem.LookupParameter("2x45Â°")
                if p_bend and p_bend.StorageType == StorageType.Integer:
                    if p_bend.AsInteger() == 1:
                        debug(" -> Turning OFF 2x45Â°")
                        t = Transaction(doc, "Turn off 2x45Â°")
                        t.Start()
                        p_bend.Set(0)
                        t.Commit()
                        updated += 1
                    elif not reducer_fixed:
                        debug(" -> 2x45Â° already OFF")
                        skipped += 1
                elif not reducer_fixed:
                    skipped += 1

                # Auto toggle 2x45 degree for elbows based on vertical pipe diameter
                if isinstance(elem, FamilyInstance):
                    fam_name = elem.Symbol.Family.Name.lower()
                    if "bocht_sh_geb" in fam_name or "bocht" in fam_name:
                        connector_mgr = elem.MEPModel.ConnectorManager
                        vertical_diam = None

                        for conn in connector_mgr.Connectors:
                            dir = conn.CoordinateSystem.BasisZ
                            if abs(dir.Z) > 0.9:
                                try:
                                    connected = list(conn.AllRefs)
                                    for ref in connected:
                                        if ref.Owner.Id != elem.Id and hasattr(
                                            ref.Owner, "LookupParameter"
                                        ):
                                            pipe = ref.Owner
                                            diam_param = pipe.LookupParameter(
                                                "Outside Diameter"
                                            ) or pipe.LookupParameter("Diameter")
                                            if diam_param:
                                                d_mm = diam_param.AsDouble() * 304.8
                                                vertical_diam = d_mm
                                                break
                                except Exception as ex:
                                    debug(
                                        "âš ï¸ Failed to resolve vertical pipe diameter:",
                                        ex,
                                    )

                        if vertical_diam is not None:
                            try:
                                bend_param = elem.LookupParameter("2x45Â°")
                                if (
                                    bend_param
                                    and bend_param.StorageType == StorageType.Integer
                                ):
                                    t = Transaction(doc, "Set 2x45Â° for elbow")
                                    t.Start()
                                    if vertical_diam > 100:
                                        bend_param.Set(1)
                                        debug(
                                            "âœ… 2x45Â° turned ON for:",
                                            elem.Id,
                                            "| Ã˜ =",
                                            vertical_diam,
                                        )
                                    else:
                                        bend_param.Set(0)
                                        debug(
                                            "âœ… 2x45Â° turned OFF for:",
                                            elem.Id,
                                            "| Ã˜ =",
                                            vertical_diam,
                                        )
                                    t.Commit()
                                    updated += 1
                            except Exception as ex:
                                debug("âŒ Failed to set 2x45Â° on elbow:", ex)

                    # Auto toggle reducer_eccentric for multireducer going UP
                    elif "multireducer_geb" in fam_name:
                        try:
                            connector_mgr = elem.MEPModel.ConnectorManager
                            is_vertical_up = False

                            for conn in connector_mgr.Connectors:
                                dir = conn.CoordinateSystem.BasisZ
                                if abs(dir.Z) > 0.9:
                                    connected = list(conn.AllRefs)
                                    for ref in connected:
                                        if ref.Owner.Id != elem.Id:
                                            # Check if the direction is upward
                                            vec = conn.CoordinateSystem.BasisZ
                                            if vec.Z > 0.9:
                                                is_vertical_up = True
                                                break
                                    if is_vertical_up:
                                        break

                            if is_vertical_up:
                                reducer_param = elem.LookupParameter(
                                    "reducer_eccentric"
                                )
                                if reducer_param and reducer_param.AsInteger() == 1:
                                    t = Transaction(doc, "Auto-Fix Reducer Eccentric")
                                    t.Start()
                                    reducer_param.Set(0)
                                    t.Commit()
                                    debug(
                                        "âœ… Turned OFF reducer_eccentric for vertical-up multireducer:",
                                        elem.Id,
                                    )
                                    updated += 1
                        except Exception as ex:
                            debug("âŒ Failed to auto-toggle reducer_eccentric:", ex)

            except Exception as ex:
                debug("Exception while processing:", ex)
                skipped += 1

        return updated, skipped

    def btnFixReducers_Click(self, sender, event):

        updated, skipped = self.auto_fix_inline()

        for row in self.dataGrid.Rows:
            cat = row.Cells["Category"].Value
            if cat != "Pipe Fittings":
                continue

            try:
                eid = int(str(row.Cells["Id"].Value))
                elem = doc.GetElement(ElementId(eid))
                if not elem:
                    continue

                name = row.Cells["Name"].Value
                tag_status = row.Cells["TagStatus"].Value

                # Flip 2x45Â° logic (manual logic reused)
                if tag_status == "Flip 2x45Â°":
                    debug(">> Activating Flip 2x45Â° for:", name)
                    if isinstance(elem, FamilyInstance):
                        try:
                            bend_param = elem.LookupParameter("bend_visible")
                            preserve_param = elem.LookupParameter(
                                "bend_visible_preserve"
                            )

                            if (
                                bend_param
                                and bend_param.StorageType == StorageType.Integer
                            ):
                                bend_param.Set(0)
                                debug(" -> bend_visible OFF")

                            if (
                                preserve_param
                                and preserve_param.StorageType == StorageType.Integer
                            ):
                                preserve_param.Set(0)
                                debug(" -> bend_visible_preserve OFF")

                            debug("âœ… Flip 2x45Â° applied to:", eid)
                        except Exception as e:
                            debug(
                                "âš ï¸ Failed to flip 2x45Â° for",
                                eid,
                                "Error:",
                                str(e),
                            )

                # New: Vertical Reducer Detection & Fix
                name_lc = name.lower()
                if "multireducer_geb" in name_lc and "var. dn/od" in name_lc:
                    try:
                        is_vertical = False
                        if hasattr(elem, "HandOrientation"):
                            dir = elem.HandOrientation
                            debug("Orientation vector (HandOrientation):", dir)
                            if abs(dir.Z) > 0.9:
                                is_vertical = True

                        debug(
                            "Checking vertical reducer:",
                            eid,
                            "| is_vertical =",
                            is_vertical,
                        )

                        if is_vertical:
                            reducer_param = elem.LookupParameter("reducer_eccentric")
                            geom_param = elem.LookupParameter("geom_exc")

                            t = Transaction(doc, "Fix Vertical Reducer")
                            t.Start()

                            if reducer_param and reducer_param.AsInteger() == 1:
                                reducer_param.Set(0)
                                debug(" -> reducer_eccentric turned OFF")

                            if geom_param and geom_param.AsInteger() == 1:
                                geom_param.Set(0)
                                debug(" -> geom_exc turned OFF")

                            t.Commit()
                            debug("âœ… Fixed vertical reducer for:", eid)
                    except Exception as ve:
                        debug(
                            "âŒ Error fixing vertical reducer for:", eid, "Error:", ve
                        )

                # Re-read parameters from Revit
                p_warn = elem.LookupParameter("waarschuwing")
                warning_val = p_warn.AsString() if p_warn else ""
                row.Cells["Warning"].Value = warning_val

                p_bend = elem.LookupParameter("2x45Â°")
                if p_bend and p_bend.StorageType == StorageType.Integer:
                    bend45_val = "Yes" if p_bend.AsInteger() == 1 else "No"
                    row.Cells["Bend45"].Value = bend45_val
            except:
                continue

        MessageBox.Show(
            "âœ… Reducers Fixed!\n\nUpdated: {}\nSkipped: {}".format(updated, skipped),
            "Fix Reducers",
        )

    def clear_placeholder(self, sender, event):
        if self.txtTextNoteCode.Text == "prefab 5.5.5":
            self.txtTextNoteCode.Text = ""
            self.txtTextNoteCode.ForeColor = Color.Black

    def restore_placeholder(self, sender, event):
        if self.txtTextNoteCode.Text.strip() == "":
            self.txtTextNoteCode.Text = "prefab 5.5.5"
            self.txtTextNoteCode.ForeColor = Color.Gray

    def _autofill_codes_before_tagging_if_needed(self):
        base = normalize_code(self.txtTextNoteCode.Text or "")
        if not base:
            return
        needs_fill = False
        for i in range(self.dataGrid.Rows.Count):
            row = self.dataGrid.Rows[i]
            cat = row.Cells["Category"].Value
            if cat != "Pipes":
                continue
            val = (row.Cells["NewCode"].Value or "").strip()
            if not val.startswith(base + "."):
                needs_fill = True
                break
        if needs_fill:
            self.autoFillPipeTagCodes(None, None)
            print("TAG ADD/REMOVE -> auto-filled NewCode from text note before tagging")

    def _existing_pipe_tag_for_host(self, host_id):
        for tag in (
            FilteredElementCollector(doc, doc.ActiveView.Id)
            .OfCategory(BuiltInCategory.OST_PipeTags)
            .WhereElementIsNotElementType()
            .ToElements()
        ):
            try:
                tagged = (
                    tag.GetTaggedElementIds()
                    if hasattr(tag, "GetTaggedElementIds")
                    else [tag.TaggedElementId]
                )
                for rid in tagged:
                    eid = (
                        rid.HostElementId.IntegerValue
                        if hasattr(rid, "HostElementId")
                        else rid.IntegerValue
                    )
                    if eid == host_id:
                        return tag
            except Exception:
                continue
        return None

    def remove_pipe_tag_for_row(self, row):
        host_id = int(str(row.Cells["Id"].Value))
        existing_tag = self._existing_pipe_tag_for_host(host_id)
        removed = 0
        if existing_tag:
            tr = Transaction(doc, "Remove Pipe Tag")
            try:
                tr.Start()
                tag_id = existing_tag.Id
                doc.Delete(tag_id)
                print("TAG ADD/REMOVE -> removed tag id:", tag_id.IntegerValue)
                tr.Commit()
                removed = 1
            except Exception:
                rollback_transaction(tr)
                raise
        row.Cells["TagStatus"].Value = "Add/Place Tag"
        row.Cells["TagStatus"].ReadOnly = False
        for i in range(self.dataGrid.Rows.Count - 1, -1, -1):
            r2 = self.dataGrid.Rows[i]
            try:
                if r2.Cells["Category"].Value == "Pipe Tags":
                    tag_elem = doc.GetElement(ElementId(int(str(r2.Cells["Id"].Value))))
                    if not tag_elem:
                        self.dataGrid.Rows.RemoveAt(i)
                        continue
                    host = get_pipe_host_from_tag(tag_elem)
                    if host and host.Id.IntegerValue == host_id:
                        self.dataGrid.Rows.RemoveAt(i)
            except Exception:
                continue
        return removed

    def ensure_pipe_tag_for_row(self, row):
        self._autofill_codes_before_tagging_if_needed()
        host_id = int(str(row.Cells["Id"].Value))
        host = doc.GetElement(ElementId(host_id))
        if not host:
            return None
        row_new_code = (row.Cells["NewCode"].Value or "").strip()
        new_tag = None
        tr = Transaction(doc, "Ensure Pipe Tag")
        try:
            tr.Start()
            hp = host.LookupParameter("Comments")
            host_before = ""
            try:
                host_before = hp.AsString() or "" if hp else ""
            except Exception:
                host_before = ""
            if hp and not hp.IsReadOnly and row_new_code:
                hp.Set(row_new_code)
            doc.Regenerate()
            print("TAG ADD/REMOVE -> host id:", host_id)
            print("TAG ADD/REMOVE -> row NewCode:", row_new_code)
            print("TAG ADD/REMOVE -> host Comments before:", host_before)
            try:
                print(
                    "TAG ADD/REMOVE -> host Comments after:",
                    hp.AsString() or "" if hp else "",
                )
            except Exception:
                print("TAG ADD/REMOVE -> host Comments after:", "")

            existing_tag = self._existing_pipe_tag_for_host(host_id)
            if existing_tag:
                print(
                    "TAG ADD/REMOVE -> existing tag reused:",
                    existing_tag.Id.IntegerValue,
                )
                new_tag = existing_tag
            else:
                bb = host.get_BoundingBox(uidoc.ActiveView)
                if bb:
                    ctr = XYZ(
                        (bb.Min.X + bb.Max.X) / 2.0,
                        (bb.Min.Y + bb.Max.Y) / 2.0,
                        (bb.Min.Z + bb.Max.Z) / 2.0,
                    )
                    new_tag = IndependentTag.Create(
                        doc,
                        doc.ActiveView.Id,
                        Reference(host),
                        True,
                        TagMode.TM_ADDBY_CATEGORY,
                        TagOrientation.Horizontal,
                        ctr,
                    )
            try:
                tp = new_tag.LookupParameter("Comments") if new_tag else None
                if tp and not tp.IsReadOnly and row_new_code:
                    tp.Set(row_new_code)
            except Exception:
                pass
            print(
                "TAG ADD/REMOVE -> tag id:", new_tag.Id.IntegerValue if new_tag else ""
            )
            tr.Commit()
        except Exception:
            rollback_transaction(tr)
            raise

        row.Cells["TagStatus"].Value = "Remove Tag"
        row.Cells["TagStatus"].ReadOnly = False
        if new_tag:
            te = doc.GetElement(new_tag.Id)
            if te:
                existing_row = False
                for i in range(self.dataGrid.Rows.Count):
                    r2 = self.dataGrid.Rows[i]
                    try:
                        if (
                            r2.Cells["Category"].Value == "Pipe Tags"
                            and int(str(r2.Cells["Id"].Value)) == te.Id.IntegerValue
                        ):
                            existing_row = True
                            break
                    except Exception:
                        continue
                if not existing_row:
                    data = {
                        "Id": str(te.Id),
                        "Category": "Pipe Tags",
                        "Name": te.Name or "",
                        "DefaultCode": host.LookupParameter("Comments").AsString()
                        or "",
                        "NewCode": row.Cells["NewCode"].Value,
                        "OutsideDiameter": row.Cells["OutsideDiameter"].Value,
                        "Length": row.Cells["Length"].Value,
                        "Size": "",
                        "GEB_Article_Number": "",
                        "TagStatus": "Yes",
                    }
                    self._add_row(data)
        return new_tag

    def bulkAddRemoveTags_Click(self, sender, event):
        rows_to_process = []
        for row in self.dataGrid.Rows:
            cat = row.Cells["Category"].Value
            if cat == "Pipes":
                rows_to_process.append(row)

        if not rows_to_process:
            return

        all_have_tags = True
        for row in rows_to_process:
            try:
                host_id = int(str(row.Cells["Id"].Value))
                if self._existing_pipe_tag_for_host(host_id) is None:
                    all_have_tags = False
                    break
            except Exception:
                all_have_tags = False
                break

        created = 0
        reused = 0
        removed = 0
        skipped = 0
        if all_have_tags:
            print("TAG ADD/REMOVE MODE -> remove existing tags")
            for row in rows_to_process:
                try:
                    removed += self.remove_pipe_tag_for_row(row)
                except Exception as ex:
                    skipped += 1
                    print("TAG ADD/REMOVE -> skipped:", ex)
        else:
            print("TAG ADD/REMOVE MODE -> add/reuse missing tags")
            for row in rows_to_process:
                try:
                    host_id = int(str(row.Cells["Id"].Value))
                    existing = self._existing_pipe_tag_for_host(host_id)
                    tag = self.ensure_pipe_tag_for_row(row)
                    if existing:
                        reused += 1
                    elif tag:
                        created += 1
                    else:
                        skipped += 1
                except Exception as ex:
                    skipped += 1
                    print("TAG ADD/REMOVE -> skipped:", ex)
        print("TAG ADD/REMOVE SUMMARY -> created:", created)
        print("TAG ADD/REMOVE SUMMARY -> reused:", reused)
        print("TAG ADD/REMOVE SUMMARY -> removed:", removed)
        print("TAG ADD/REMOVE SUMMARY -> skipped:", skipped)

    # Smart dynamic spacing
    def rearrange_buttons(self, sender, event):
        controls = list(self.buttonPanel.Controls)
        total_width = sum(c.Width for c in controls)
        available = self.buttonPanel.Width - total_width
        spacing = max(10, available // (len(controls) + 1))
        x = spacing
        for ctrl in controls:
            ctrl.Location = Point(x, (self.buttonPanel.Height - ctrl.Height) // 2)
            x += ctrl.Width + spacing

    def _add_row(self, data):
        """Helper to append a new DataGridView row from a dict."""
        idx = self.dataGrid.Rows.Add()
        row = self.dataGrid.Rows[idx]
        for k, v in data.items():
            row.Cells[k].Value = v

        # Start with default
        row.Cells["TagStatus"].Value = "Remove Tag"
        row.Cells["TagStatus"].ReadOnly = True

        # Try to identify reducer buttons
        try:
            cat = data.get("Category", "")
            eid = int(data.get("Id", "0"))
            elem = doc.GetElement(ElementId(eid))

            if cat == "Pipe Fittings" and isinstance(elem, FamilyInstance):
                fam_name = elem.Symbol.Family.Name.lower()
                debug("âœ… Family name:", fam_name)

                if "multireducer_geb" in fam_name:
                    row.Cells["TagStatus"].Value = "Flip Reducer"
                    row.Cells["TagStatus"].ReadOnly = False

                elif "multibocht" in fam_name:
                    row.Cells["TagStatus"].Value = "Flip 2x45Â°"
                    row.Cells["TagStatus"].ReadOnly = False

                elif "liggend" in fam_name:
                    row.Cells["TagStatus"].Value = "Flip T-stuk"
                    row.Cells["TagStatus"].ReadOnly = False

        except Exception as ex:
            debug("âš ï¸ Error resolving Flip button logic:", ex)

    def btnPlaceTextNote_Click(self, sender, event):
        text_note_code = self.txtTextNoteCode.Text.strip()
        if text_note_code == "":
            MessageBox.Show("Please enter a Text Note Code.", "Error")
            return
        if not self.regionElements or len(self.regionElements) == 0:
            MessageBox.Show(
                "Region elements not available to compute location.", "Error"
            )
            return
        region_min, region_max = get_region_bounding_box(self.regionElements)
        corner = region_min
        ttn = Transaction(doc, "Place Text Note")
        ttn.Start()
        note_type = FilteredElementCollector(doc).OfClass(TextNoteType).FirstElement()
        if note_type:
            opts = TextNoteOptions(note_type.Id)
            new_note = TextNote.Create(
                doc, doc.ActiveView.Id, corner, text_note_code, opts
            )
            if new_note:
                MessageBox.Show("Text Note created successfully.", "Success")
                self.textNotePlaced = True
        else:
            MessageBox.Show("No TextNoteType found.", "Error")
        ttn.Commit()

    def autoFillPipeTagCodes(self, sender, event):
        # 1) Parse base
        raw = self.txtTextNoteCode.Text.strip()
        m = re.search(r"([\d\.]+)", raw)
        if not m:
            MessageBox.Show("Could not parse base code from text note.", "Error")
            return
        base = m.group(1)  # e.g. "4.1.1"

        # 2) (optional) keep prefix/base_n split if you want for pipes
        parts = base.split(".")
        if len(parts) >= 3:
            prefix = parts[0] + "." + parts[1]  # "4.1"
            try:
                base_n = int(parts[2])  # 1
            except:
                base_n = 0
        else:
            prefix, base_n = base, 0

        # 2) Collect indices
        fit_rows, pipe_rows, tag_rows = [], [], []
        for i in range(self.dataGrid.Rows.Count):
            cat = self.dataGrid.Rows[i].Cells["Category"].Value
            if cat == "Pipe Fittings":
                fit_rows.append(i)
            elif cat == "Pipes":
                pipe_rows.append(i)
            elif cat == "Pipe Tags":
                tag_rows.append(i)

        # Override all Pipe Fittings rows to the base code
        for idx in fit_rows:
            self.dataGrid.Rows[idx].Cells["NewCode"].Value = base

        # 5) Pipes sorted and numbered: full base + .1,.2...
        pipe_centers = []
        for idx in pipe_rows:
            rid = int(str(self.dataGrid.Rows[idx].Cells["Id"].Value))
            elem = doc.GetElement(ElementId(rid))
            bbox = elem.get_BoundingBox(uidoc.ActiveView)
            if bbox:
                ctr = XYZ(
                    (bbox.Min.X + bbox.Max.X) * 0.5,
                    (bbox.Min.Y + bbox.Max.Y) * 0.5,
                    (bbox.Min.Z + bbox.Max.Z) * 0.5,
                )
            else:
                ctr = XYZ(0, 0, 0)
            pipe_centers.append((idx, ctr))

        pipe_centers.sort(key=lambda x: (x[1].X, x[1].Y))
        for i, (idx, _) in enumerate(pipe_centers, 1):
            self.dataGrid.Rows[idx].Cells["NewCode"].Value = "{}.{}".format(base, i)

        # 6) Mirror pipe numbering onto pipeâ€tag rows (same count)
        for i, _ in enumerate(pipe_centers, 1):
            if i - 1 < len(tag_rows):
                trow = tag_rows[i - 1]
                self.dataGrid.Rows[trow].Cells["NewCode"].Value = "{}.{}".format(
                    base, i
                )

    def dataGrid_CellContentClick(self, sender, e):
        col = self.dataGrid.Columns[e.ColumnIndex].Name
        if col != "TagStatus":
            return

        # Always include the clicked row
        clicked_row = self.dataGrid.Rows[e.RowIndex]
        selected_indexes = {r.Index for r in self.dataGrid.SelectedRows if r.Index >= 0}
        selected_indexes.add(clicked_row.Index)

        # Build selected rows list from indexes
        selected_rows = [self.dataGrid.Rows[i] for i in selected_indexes]

        for row in selected_rows:
            cat = row.Cells["Category"].Value
            val = row.Cells["TagStatus"].Value

            if cat == "Pipe Fittings" and val == "Flip T-stuk":
                try:
                    host_id = int(str(row.Cells["Id"].Value))
                    elem = doc.GetElement(ElementId(host_id))
                    if elem:
                        p = elem.LookupParameter("switch_excentriciteit")
                        if p and p.StorageType == StorageType.Integer:
                            current = p.AsInteger()
                            t = Transaction(doc, "Flip T-stuk (switch_excentriciteit)")
                            t.Start()
                            p.Set(0 if current == 1 else 1)
                            t.Commit()
                            debug(
                                "ðŸ”„ Flipped T-stuk (switch_excentriciteit = %s): %s"
                                % (str(not current), str(elem.Id))
                            )
                except Exception as ex:
                    debug("âŒ Failed to flip T-stuk using switch_excentriciteit:", ex)

            elif cat == "Pipe Fittings" and val == "Flip 2x45Â°":
                try:
                    host_id = int(str(row.Cells["Id"].Value))
                    elem = doc.GetElement(ElementId(host_id))
                    if elem:
                        param = elem.LookupParameter("2x45Â°")
                        if param and param.StorageType == StorageType.Integer:
                            current_val = param.AsInteger()

                            t = Transaction(doc, "Toggle 2x45Â°")
                            t.Start()
                            param.Set(0 if current_val == 1 else 1)
                            t.Commit()

                            if current_val == 1:
                                row.Cells["Bend45"].Value = "No"
                            else:
                                row.Cells["Bend45"].Value = "Yes"

                            debug(
                                "âœ… Toggled 2x45Â° to",
                                "OFF" if current_val == 1 else "ON",
                                "for:",
                                host_id,
                            )
                        else:
                            debug(
                                "âš ï¸ Parameter '2x45Â°' not found or not boolean:",
                                host_id,
                            )
                    else:
                        debug("âš ï¸ Could not get element from ID:", host_id)
                except Exception as ex:
                    debug("âŒ Failed to flip 2x45Â°:", ex)

            elif cat == "Pipe Fittings" and val == "Flip Reducer":
                try:
                    host_id = int(str(row.Cells["Id"].Value))
                    elem = doc.GetElement(ElementId(host_id))

                    if elem and isinstance(elem, FamilyInstance):
                        name = row.Cells["Name"].Value or ""
                        family_name = elem.Symbol.Family.Name.lower()

                        if "multireducer_geb" in family_name:
                            reducer_param = elem.LookupParameter("reducer_eccentric")
                            if (
                                reducer_param
                                and reducer_param.StorageType == StorageType.Integer
                            ):
                                current_val = reducer_param.AsInteger()

                                t = Transaction(doc, "Toggle reducer_eccentric")
                                t.Start()
                                reducer_param.Set(0 if current_val == 1 else 1)
                                t.Commit()

                                debug(
                                    "âœ… Toggled reducer_eccentric to",
                                    "OFF" if current_val == 1 else "ON",
                                    "for:",
                                    host_id,
                                )
                except Exception as ex:
                    debug("âŒ Failed to toggle redcuer_eccentric:", ex)

            # ----------------------
            # ADD/REMOVE TAG (Pipes)
            # ----------------------
            elif cat == "Pipes":
                host_id = int(str(row.Cells["Id"].Value))
                existing = self._existing_pipe_tag_for_host(host_id)
                if existing:
                    print("TAG ADD/REMOVE MODE -> remove existing tags")
                    removed = self.remove_pipe_tag_for_row(row)
                    print("TAG ADD/REMOVE SUMMARY -> created:", 0)
                    print("TAG ADD/REMOVE SUMMARY -> reused:", 0)
                    print("TAG ADD/REMOVE SUMMARY -> removed:", removed)
                    print("TAG ADD/REMOVE SUMMARY -> skipped:", 0)
                else:
                    print("TAG ADD/REMOVE MODE -> add/reuse missing tags")
                    tag = self.ensure_pipe_tag_for_row(row)
                    print("TAG ADD/REMOVE SUMMARY -> created:", 1 if tag else 0)
                    print("TAG ADD/REMOVE SUMMARY -> reused:", 0)
                    print("TAG ADD/REMOVE SUMMARY -> removed:", 0)
                    print("TAG ADD/REMOVE SUMMARY -> skipped:", 0 if tag else 1)
            # --------------------------
            # REMOVE TAG (Pipe Tags)
            # --------------------------
            elif cat == "Pipe Tags" and val == "Remove Tag":
                tag_id = ElementId(int(str(row.Cells["Id"].Value)))
                self.dataGrid.SelectionChanged -= self.on_row_selected
                try:
                    tag_elem = doc.GetElement(tag_id)
                    host_id = None
                    if tag_elem:
                        ids = (
                            tag_elem.GetTaggedElementIds()
                            if hasattr(tag_elem, "GetTaggedElementIds")
                            else [tag_elem.TaggedElementId]
                        )
                        if ids and len(ids):
                            rid = ids[0]
                            host_id = (
                                rid.HostElementId.IntegerValue
                                if hasattr(rid, "HostElementId")
                                else rid.IntegerValue
                            )

                    tr = Transaction(doc, "Remove Pipe-Tag")
                    tr.Start()
                    doc.Delete(tag_id)
                    tr.Commit()

                    self.dataGrid.Rows.RemoveAt(row.Index)

                    if host_id:
                        for i in range(self.dataGrid.Rows.Count):
                            pr = self.dataGrid.Rows[i]
                            if (
                                int(str(pr.Cells["Id"].Value)) == host_id
                                and pr.Cells["Category"].Value == "Pipes"
                            ):
                                pr.Cells["TagStatus"].Value = "Add/Place Tag"
                                pr.Cells["TagStatus"].ReadOnly = False
                                break
                finally:
                    self.dataGrid.SelectionChanged += self.on_row_selected

    def okButton_Click(self, sender, event):
        updated_data = []
        for row in self.dataGrid.Rows:
            entry = {
                "Id": row.Cells["Id"].Value,
                "Category": row.Cells["Category"].Value,
                "Name": row.Cells["Name"].Value,
                "DefaultCode": row.Cells["DefaultCode"].Value,
                "NewCode": row.Cells["NewCode"].Value,
                "OutsideDiameter": row.Cells["OutsideDiameter"].Value,
                "Length": row.Cells["Length"].Value,
                "TagStatus": row.Cells["TagStatus"].Value,
            }
            updated_data.append(entry)

        self.Result = {
            "Elements": updated_data,
            "TextNotePlaced": self.textNotePlaced,
            "TextNote": self.txtTextNoteCode.Text.strip(),
        }
        self.DialogResult = DialogResult.OK
        self.Close()

    def on_row_selected(self, sender, event):
        """When the user clicks or arrows to a row, select that element in Revit."""
        row = self.dataGrid.CurrentRow
        if not row:
            return
        id_val = row.Cells["Id"].Value
        if not id_val:
            return

        # try to parse and highlight, but swallow any invalid-object errors
        try:
            eid = ElementId(int(str(id_val)))
            elem = doc.GetElement(eid)
            # guard against deleted/invalid elements
            if elem and elem.IsValidObject:
                uidoc.Selection.SetElementIds(List[ElementId]([eid]))
        except:
            return


def show_element_editor(elements_data, region_elements=None):
    form = ElementEditorForm(elements_data, region_elements)
    if form.ShowDialog() == DialogResult.OK:
        return form.Result
    return None


# ==================================================
# Filter Gathered Elements to Relevant Categories
# ==================================================
def filter_relevant_elements(gathered_elements):
    """
    Build a list of dicts with keys:
     "Id","Category","Name","DefaultCode","NewCode",
     "OutsideDiameter","Length","GEB_Article_Number","TagStatus"
    """
    relevant = []

    pipe_ids = {
        e.Id.IntegerValue
        for e in gathered_elements
        if e.Category and e.Category.Name == "Pipes"
    }
    # grab all tags in the view
    all_pipe_tags = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_PipeTags)
        .WhereElementIsNotElementType()
        .ToElements()
    )

    # pull in any tags whose host pipe was in our region
    for tag in all_pipe_tags:
        try:
            host = None
            if hasattr(tag, "GetTaggedElementIds"):
                ids = tag.GetTaggedElementIds()
                if ids and ids.Count > 0:
                    host = doc.GetElement(ids[0])
            elif hasattr(tag, "TaggedElementId"):
                host = doc.GetElement(tag.TaggedElementId)
            if host and host.Id.IntegerValue in pipe_ids:
                # and only if we haven't already added it in gathered_elements
                if not any(str(tag.Id) == d["Id"] for d in relevant):
                    warning_val = ""
                    bend45_val = ""
                    # build your dict exactly like you do for pipeâ€‘tags below
                    relevant.append(
                        {
                            "Id": str(tag.Id),
                            "Category": "Pipe Tags",
                            "Name": tag.Name or "",
                            "Warning": "",
                            "Bend45": "",
                            "DefaultCode": host.LookupParameter("Comments").AsString()
                            or "",
                            "NewCode": host.LookupParameter("Comments").AsString()
                            or "",
                            "OutsideDiameter": convert_param_to_string(
                                host.LookupParameter("Outside Diameter")
                            ),
                            "Length": convert_param_to_string(
                                host.LookupParameter("Length")
                            ),
                            "Size": "",  # if you want
                            "GEB_Article_Number": "",
                            "TagStatus": "Yes",
                        }
                    )
        except:
            pass

    for e in gathered_elements:
        if not e.Category:
            continue
        cat = e.Category.Name
        if cat not in ("Pipes", "Pipe Fittings", "Pipe Tags", "Text Notes"):
            continue

        com = e.LookupParameter("Comments")
        default_code = com.AsString() if com and com.AsString() else ""

        # initialize
        warning_val = ""
        bend45_val = ""
        outside_diam = ""
        length_val = ""
        art_num = ""
        tag_status = ""
        size_val = ""

        # --- Pipes ---
        if cat == "Pipes":
            odp = e.LookupParameter("Outside Diameter")
            lp = e.LookupParameter("Length")
            outside_diam = convert_param_to_string(odp)
            length_val = convert_param_to_string(lp)

            # detect existing tags
            tag_status = "No"
            for tag in all_pipe_tags:
                try:
                    tagged_ids = []
                    if hasattr(tag, "GetTaggedElementIds"):
                        tagged_ids = tag.GetTaggedElementIds()
                    elif hasattr(tag, "TaggedElementId"):
                        tagged_ids = [tag.TaggedElementId]
                    for tid in tagged_ids:
                        if tid and tid.IntegerValue == e.Id.IntegerValue:
                            tag_status = "Yes"
                            break
                    if tag_status == "Yes":
                        break
                except:
                    pass

        # --- Pipe Fittings ---
        elif cat == "Pipe Fittings":
            p_warn = e.LookupParameter("waarschuwing")
            warning_val = p_warn.AsString() if p_warn else ""

            p_bend = e.LookupParameter("2x45Â°")
            bend45_val = ""
            if p_bend and p_bend.StorageType == StorageType.Integer:
                bend45_val = "Yes" if p_bend.AsInteger() == 1 else "No"
            # diameter (try several names)
            for pname in ("Outside Diameter", "Diameter", "Nominal Diameter"):
                p = e.LookupParameter(pname)
                if p:
                    outside_diam = convert_param_to_string(p)
                    break
            # length
            lp = e.LookupParameter("Length")
            length_val = convert_param_to_string(lp)
            # GEB article
            ap = e.LookupParameter("GEB_Article_Number")
            art_num = ap.AsString() if ap and ap.AsString() else ""

            # only the specific fitting gets Add/Place Tag
            if e.Name and e.Name.find("DN") >= 0:
                tag_status = "No"
            else:
                tag_status = ""

        # --- Pipe Tags ---
        elif cat == "Pipe Tags":
            tag_status = "Yes"
            host = None
            try:
                if hasattr(e, "GetTaggedElementIds"):
                    ids = e.GetTaggedElementIds()
                    if ids and ids.Count > 0:
                        host = doc.GetElement(ids[0])
                if not host and hasattr(e, "TaggedElementId"):
                    host = doc.GetElement(e.TaggedElementId)
            except:
                host = None

            if host:
                odp = host.LookupParameter("Outside Diameter")
                lp = host.LookupParameter("Length")
                outside_diam = convert_param_to_string(odp)
                length_val = convert_param_to_string(lp)

        # --- Text Notes & others ---
        else:
            tag_status = ""

        size_val = ""
        if cat == "Pipe Fittings":
            param_size = e.LookupParameter("Size")
            if param_size:
                size_val = convert_param_to_string(param_size)

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
                "Size": size_val,
                "GEB_Article_Number": art_num,
                "TagStatus": tag_status,
            }
        )

    return relevant


def get_pipe_host_from_tag(tag):
    try:
        if hasattr(tag, "GetTaggedElementIds"):
            ids = tag.GetTaggedElementIds()
            if ids and ids.Count > 0:
                rid = ids[0]
                hid = rid.HostElementId if hasattr(rid, "HostElementId") else rid
                return doc.GetElement(hid)
        if hasattr(tag, "TaggedElementId"):
            return doc.GetElement(tag.TaggedElementId)
    except Exception:
        pass
    return None


def _row_element(row):
    try:
        return doc.GetElement(ElementId(int(str(row.get("Id")))))
    except Exception:
        return None


def apply_canonical_prefab_numbering(result, base_code, source_view):
    print("NUMBERING CANONICAL -> base code:", base_code)
    rows = result.get("Elements", [])
    pipe_entries = []
    pipe_code_by_id = {}
    fitting_count = 0
    for idx, row in enumerate(rows):
        elem = _row_element(row)
        if not elem:
            continue
        cat = row.get("Category")
        if cat == "Pipes":
            pt = get_element_annotation_point(elem, source_view)
            pipe_entries.append((idx, elem, pt))
        elif cat == "Pipe Fittings":
            row["NewCode"] = base_code
            fitting_count += 1
            print(
                "NUMBERING CANONICAL -> fitting element id -> NewCode:",
                elem.Id.IntegerValue,
                row["NewCode"],
            )

    pipe_entries.sort(key=lambda x: (x[2].X, x[2].Y, x[1].Id.IntegerValue))
    for n, (idx, elem, _pt) in enumerate(pipe_entries, 1):
        code = "{}.{}".format(base_code, n)
        rows[idx]["NewCode"] = code
        pipe_code_by_id[elem.Id.IntegerValue] = code
        print(
            "NUMBERING CANONICAL -> pipe element id -> NewCode:",
            elem.Id.IntegerValue,
            code,
        )

    pipe_tag_count = 0
    for row in rows:
        if row.get("Category") != "Pipe Tags":
            continue
        elem = _row_element(row)
        host = get_pipe_host_from_tag(elem) if elem else None
        host_id = host.Id.IntegerValue if host else None
        if host_id in pipe_code_by_id:
            row["NewCode"] = pipe_code_by_id[host_id]
        elif not (row.get("NewCode") or "").strip():
            row["NewCode"] = base_code
        pipe_tag_count += 1
        print(
            "NUMBERING CANONICAL -> pipe tag id -> host id -> NewCode:",
            elem.Id.IntegerValue if elem else row.get("Id"),
            host_id,
            row.get("NewCode"),
        )

    print("NUMBERING CANONICAL SUMMARY -> pipes:", len(pipe_entries))
    print("NUMBERING CANONICAL SUMMARY -> fittings:", fitting_count)
    print("NUMBERING CANONICAL SUMMARY -> pipe tags:", pipe_tag_count)


def sync_comments_from_result(result, base_code):
    t = Transaction(doc, "Sync Comments From Sheet Generator Result")
    comments_counts = {"Pipes": 0, "Pipe Fittings": 0, "Pipe Tags": 0}
    distinct_pipe_codes = set()
    verify_ids = []
    try:
        t.Start()
        for row in result.get("Elements", []):
            elem = _row_element(row)
            if not elem:
                continue
            cat = row.get("Category") or ""
            new_comment = (row.get("NewCode") or "").strip() or base_code
            p = elem.LookupParameter("Comments")
            try:
                old_comment = p.AsString() or "" if p else ""
            except Exception:
                old_comment = ""
            wrote = False
            if p and not p.IsReadOnly:
                p.Set(new_comment)
                wrote = True
            if cat == "Pipe Tags":
                host = get_pipe_host_from_tag(elem)
                if host:
                    hp = host.LookupParameter("Comments")
                    if hp and not hp.IsReadOnly:
                        hp.Set(new_comment)
            print("COMMENTS SYNC -> element id:", elem.Id.IntegerValue)
            print("COMMENTS SYNC -> category:", cat)
            print("COMMENTS SYNC -> old:", old_comment)
            print("COMMENTS SYNC -> new:", new_comment)
            print("COMMENTS SYNC -> wrote:", wrote)
            if wrote and cat in comments_counts:
                comments_counts[cat] += 1
            if cat == "Pipes":
                distinct_pipe_codes.add(new_comment)
                verify_ids.append(elem.Id)
            elif cat == "Pipe Fittings":
                verify_ids.append(elem.Id)
        doc.Regenerate()
        print("COMMENTS SYNC -> regenerate inside transaction: yes")
        t.Commit()
    except Exception:
        rollback_transaction(t)
        raise

    for eid in verify_ids:
        elem = doc.GetElement(eid)
        if elem:
            p = elem.LookupParameter("Comments")
            try:
                val = p.AsString() or "" if p else ""
            except Exception:
                val = ""
            print("COMMENTS SYNC VERIFY -> element id:", elem.Id.IntegerValue)
            print("COMMENTS SYNC VERIFY -> Comments:", val)
    print("COMMENTS SYNC SUMMARY -> pipes:", comments_counts.get("Pipes", 0))
    print("COMMENTS SYNC SUMMARY -> fittings:", comments_counts.get("Pipe Fittings", 0))
    print("COMMENTS SYNC SUMMARY -> pipe tags:", comments_counts.get("Pipe Tags", 0))
    print(
        "COMMENTS SYNC SUMMARY -> distinct pipe comment codes:",
        sorted(list(distinct_pipe_codes)),
    )


def verify_prefab_comments_before_sheet(result, base_code):
    mismatch_count = 0
    expected_pipe_codes = []
    print("PRE-SHEET VERIFY -> base code:", base_code)
    for row in result.get("Elements", []):
        elem = _row_element(row)
        if not elem:
            continue
        cat = row.get("Category") or ""
        p = elem.LookupParameter("Comments")
        try:
            val = p.AsString() or "" if p else ""
        except Exception:
            val = ""
        expected = (row.get("NewCode") or "").strip() or base_code
        if cat == "Pipes":
            expected_pipe_codes.append(expected)
            print(
                "PRE-SHEET VERIFY -> pipe ids and Comments:", elem.Id.IntegerValue, val
            )
            if val != expected:
                mismatch_count += 1
        elif cat == "Pipe Fittings":
            print(
                "PRE-SHEET VERIFY -> fitting ids and Comments:",
                elem.Id.IntegerValue,
                val,
            )
            if val != expected:
                mismatch_count += 1
        elif cat == "Pipe Tags":
            print(
                "PRE-SHEET VERIFY -> pipe tag ids and Comments:",
                elem.Id.IntegerValue,
                val,
            )
    print("PRE-SHEET VERIFY -> expected pipe codes:", expected_pipe_codes)
    print("PRE-SHEET VERIFY -> mismatch count:", mismatch_count)
    return mismatch_count


# ==================================================
# MAIN WORKFLOW
# ==================================================
gathered_elements = select_boundary_and_gather()
if gathered_elements is None or len(gathered_elements) == 0:
    MessageBox.Show("No elements were gathered. Operation cancelled.", "Error")
    sys.exit("Operation cancelled by the user.")

scanned_ids = List[ElementId]([e.Id for e in gathered_elements if e is not None])
scanned_elems = [doc.GetElement(eid) for eid in scanned_ids]

filtered_elements = filter_relevant_elements(scanned_elems)
if len(filtered_elements) == 0:
    MessageBox.Show("No relevant elements found in the selected region.", "Error")
    sys.exit("Operation cancelled by the user.")

result = show_element_editor(filtered_elements, region_elements=scanned_elems)
if result is None:
    sys.exit("Operation cancelled by the user.")

uidoc.Selection.SetElementIds(List[ElementId]())

post_editor_dialog = TaskDialog("Sheet Generator")
post_editor_dialog.MainInstruction = "Choose what to do next."
post_editor_dialog.MainContent = "You can continue with sheet creation or stop after applying tags/text in the current view."
post_editor_dialog.AddCommandLink(
    TaskDialogCommandLinkId.CommandLink1,
    "Proceed with sheet creation (duplicate view + create sheet + place schedules)",
)
post_editor_dialog.AddCommandLink(
    TaskDialogCommandLinkId.CommandLink2,
    "Only place tags/text in current view and stop (no sheet)",
)
post_editor_dialog.CommonButtons = TaskDialogCommonButtons.Cancel
post_editor_choice = post_editor_dialog.Show()
proceed_with_sheet_creation = post_editor_choice == TaskDialogResult.CommandLink1

text_note_display = (result.get("TextNote") or "").strip()
code_norm = normalize_code(text_note_display)
if not code_norm:
    MessageBox.Show("Enter a valid prefab code such as 5.5.5.", "Invalid Prefab Code")
    sys.exit("Invalid prefab code")

baseCode = code_norm
apply_canonical_prefab_numbering(result, baseCode, uidoc.ActiveView)
print("NUMBERING -> base code:", baseCode)
print(
    "NUMBERING -> pipe NewCode values:",
    [e.get("NewCode") for e in result["Elements"] if e.get("Category") == "Pipes"],
)
print(
    "NUMBERING -> fitting NewCode values:",
    [
        e.get("NewCode")
        for e in result["Elements"]
        if e.get("Category") == "Pipe Fittings"
    ],
)
print(
    "NUMBERING -> pipe tag NewCode values:",
    [e.get("NewCode") for e in result["Elements"] if e.get("Category") == "Pipe Tags"],
)
sync_comments_from_result(result, baseCode)
# --- Place the text note if not already placed ---
if not result.get("TextNotePlaced", False):
    region_min, region_max = get_region_bounding_box(gathered_elements)
    view = doc.ActiveView
    corner = region_min
    ttn = Transaction(doc, "Place Text Note at Region Corner")
    try:
        ttn.Start()
        nt = FilteredElementCollector(doc).OfClass(TextNoteType).FirstElement()
        if nt:
            opts = TextNoteOptions(nt.Id)
            TextNote.Create(
                doc,
                doc.ActiveView.Id,
                corner,
                text_note_display or code_norm,
                opts,
            )
        ttn.Commit()
    except Exception:
        rollback_transaction(ttn)
        raise

region_min, region_max = get_region_bounding_box(scanned_elems)
boundary_source_view = uidoc.ActiveView


def run_sheet_creation_workflow(
    base_code, scanned_ids, scanned_elems, region_min, region_max, source_view
):
    source_plan = resolve_dup_source_plan_view(
        doc, source_view, preferred_name_contains="V00 - C00_Prefab Sets"
    )
    if source_plan is None:
        TaskDialog.Show(
            "Sheet Generator",
            "No valid primary ViewPlan was found for duplication.",
        )
        return

    orig = source_plan

    # grab the original crop box transform so the region maps to the same coordinate system
    orig_bb = orig.CropBox
    orig_trans = orig_bb.Transform

    duplicate_option = ViewDuplicateOption.WithDetailing

    primary_view_id = "n/a"
    if hasattr(orig, "GetPrimaryViewId"):
        try:
            primary_view_id = orig.GetPrimaryViewId().IntegerValue
        except Exception:
            primary_view_id = "n/a"

    duplicate_debug_info = (
        "Source view before duplicate:\n"
        "Id: {}\n"
        "Name: {}\n"
        "ViewType: {}\n"
        "IsTemplate: {}\n"
        "PrimaryViewId: {}".format(
            orig.Id.IntegerValue,
            orig.Name,
            orig.ViewType,
            orig.IsTemplate,
            primary_view_id,
        )
    )
    print(duplicate_debug_info)
    if DEBUG_DIALOGS:
        TaskDialog.Show("Sheet Generator Debug", duplicate_debug_info)
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
            self.ClientSize = Size(300, 350)

            self.lb = ListBox()
            self.lb.Bounds = Rectangle(10, 10, 280, 280)
            for sym in tbs:
                fam = sym.FamilyName
                type_name = (
                    sym.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM).AsString()
                    or ""
                )
                self.lb.Items.Add(fam + " - " + type_name)
            self.Controls.Add(self.lb)

            ok = Button(
                Text="OK", DialogResult=DialogResult.OK, Location=Point(10, 300)
            )
            ca = Button(
                Text="Cancel",
                DialogResult=DialogResult.Cancel,
                Location=Point(100, 300),
            )
            self.Controls.Add(ok)
            self.Controls.Add(ca)
            self.AcceptButton = ok
            self.CancelButton = ca

    existing_sheets = FilteredElementCollector(doc).OfClass(ViewSheet).ToElements()
    existing_numbers = {s.SheetNumber for s in existing_sheets}

    picker = TBPicker(all_tbs)
    if picker.ShowDialog() != DialogResult.OK or picker.lb.SelectedIndex < 0:
        MessageBox.Show("Sheet creation cancelled.", "Info")
        return

    title_block = all_tbs[picker.lb.SelectedIndex]
    titleblock_type_id = title_block.Id
    if not titleblock_type_id:
        print("User cancelled titleblock selection.")
        return

    tg = TransactionGroup(doc, "Prefab Sheet Creation")
    try:
        tg.Start()

        new_view = None
        view3d = None
        sheet = None
        tb_center = None

        with Tx(doc, "Prefab - Create Views"):
            require_modifiable(doc, "Prefab - Create Views (tx start)")
            new_view, route_used = duplicate_or_recreate_plan_view(
                doc, orig, duplicate_option, base_code
            )
            print("Plan view created via: {}".format(route_used))
            require_modifiable(doc, "Prefab - Create Views / ViewTemplateId")
            new_view.ViewTemplateId = ElementId.InvalidElementId
            require_modifiable(doc, "Prefab - Create Views / Discipline")
            new_view.Discipline = ViewDiscipline.Coordination
            require_modifiable(doc, "Prefab - Create Views / Scale")
            new_view.Scale = 25

            bb = BoundingBoxXYZ()
            bb.Min = region_min
            bb.Max = region_max
            bb.Transform = orig_trans
            require_modifiable(doc, "Prefab - Create Views / CropBoxActive")
            new_view.CropBoxActive = True
            require_modifiable(doc, "Prefab - Create Views / CropBoxVisible")
            new_view.CropBoxVisible = True
            annoParam = new_view.get_Parameter(
                BuiltInParameter.VIEWER_ANNOTATION_CROP_ACTIVE
            )
            if annoParam and not annoParam.IsReadOnly:
                require_modifiable(doc, "Prefab - Create Views / AnnotationCrop")
                annoParam.Set(1)
            require_modifiable(doc, "Prefab - Create Views / CropBox")
            new_view.CropBox = bb
            param = new_view.get_Parameter(BuiltInParameter.VIEWER_CROP_REGION_VISIBLE)
            if param and not param.IsReadOnly:
                require_modifiable(doc, "Prefab - Create Views / CropRegionVisible")
                param.Set(0)
            cat_drag_controls = doc.Settings.Categories.get_Item(
                BuiltInCategory.OST_ConnectorElem
            )
            if cat_drag_controls and new_view.CanCategoryBeHidden(cat_drag_controls.Id):
                require_modifiable(doc, "Prefab - Create Views / HideConnectors")
                new_view.SetCategoryHidden(cat_drag_controls.Id, True)

            all3ds = FilteredElementCollector(doc).OfClass(ViewFamilyType).ToElements()
            v3d_type = next(
                v for v in all3ds if v.ViewFamily == ViewFamily.ThreeDimensional
            )
            require_modifiable(doc, "Prefab - Create Views / Create3D")
            view3d = View3D.CreateIsometric(doc, v3d_type.Id)
            view3d = ensure_orthographic_3d_view(doc, view3d)
            try:
                require_modifiable(doc, "Prefab - Create Views / 3DName")
                view3d.Name = "{} - Sheet".format(base_code)
            except Exception as ex:
                print("3D view naming failed for {}: {}".format(base_code, ex))
            require_modifiable(doc, "Prefab - Create Views / 3DDiscipline")
            view3d.Discipline = ViewDiscipline.Architectural
            require_modifiable(doc, "Prefab - Create Views / 3DScale")
            view3d.Scale = 25
            section_bb = BoundingBoxXYZ()
            section_bb.Min = region_min
            section_bb.Max = region_max
            require_modifiable(doc, "Prefab - Create Views / SetSectionBox")
            view3d.SetSectionBox(section_bb)
            require_modifiable(doc, "Prefab - Create Views / IsSectionBoxActive")
            view3d.IsSectionBoxActive = True
            section_box = view3d.GetSectionBox()
            if section_box:
                require_modifiable(doc, "Prefab - Create Views / SectionBoxEnabled")
                section_box.Enabled = True

        with Tx(doc, "Prefab - Create Sheet"):
            if base_code in existing_numbers:
                raise Exception("Sheet already exists: {}".format(base_code))
            require_modifiable(doc, "Prefab - Create Sheet / ViewSheet.Create")
            sheet = ViewSheet.Create(doc, titleblock_type_id)
            require_modifiable(doc, "Prefab - Create Sheet / SheetNumber")
            sheet.SheetNumber = base_code
            require_modifiable(doc, "Prefab - Create Sheet / SheetName")
            sheet.Name = "Prefab " + base_code

            titleblock_inst = next(
                (
                    e
                    for e in FilteredElementCollector(doc, sheet.Id)
                    .OfClass(FamilyInstance)
                    .ToElements()
                    if e.Symbol.Id == titleblock_type_id
                ),
                None,
            )
            tb_bb = titleblock_inst.get_BoundingBox(sheet) if titleblock_inst else None
            if not tb_bb:
                raise Exception("Could not retrieve title block bounding box.")

            tb_center = XYZ(
                (tb_bb.Min.X + tb_bb.Max.X) / 2,
                (tb_bb.Min.Y + tb_bb.Max.Y) / 2,
                0,
            )
            require_modifiable(doc, "Prefab - Create Sheet / PlanViewport")
            Viewport.Create(doc, sheet.Id, new_view.Id, tb_center)
            require_modifiable(doc, "Prefab - Create Sheet / 3DViewport")
            Viewport.Create(
                doc,
                sheet.Id,
                view3d.Id,
                XYZ(tb_center.X + 0.25, tb_center.Y + 0.25, 0),
            )

        detected_systems = detect_systems(doc, scanned_ids)
        with Tx(doc, "Prefab - Schedules"):
            require_modifiable(doc, "Prefab - Schedules (tx start)")
            created_schedule_ids = duplicate_schedules_for_systems(
                doc, detected_systems, base_code
            )

            uMin, uMax = sheet.Outline.Min.U, sheet.Outline.Max.U
            vMin, vMax = sheet.Outline.Min.V, sheet.Outline.Max.V
            w, h = (uMax - uMin), (vMax - vMin)
            x = uMin + 0.05 * w
            y_start = vMax - 0.35 * h
            spacing = 0.12 * h
            for idx, sched_id in enumerate(created_schedule_ids):
                y = y_start - idx * spacing
                require_modifiable(doc, "Prefab - Schedules / PlaceSchedule")
                ScheduleSheetInstance.Create(doc, sheet.Id, sched_id, XYZ(x, y, 0))

        with Tx(doc, "Center Views and Schedules on Sheet"):
            require_modifiable(doc, "Center Views and Schedules on Sheet (tx start)")
            viewports = (
                FilteredElementCollector(doc, sheet.Id).OfClass(Viewport).ToElements()
            )
            for vp in viewports:
                require_modifiable(doc, "Center Views and Schedules / SetBoxCenter")
                vp.SetBoxCenter(tb_center)

        tg.Assimilate()

        # ---- Phase B: annotate 3D AFTER creation is committed ----
        if ANNOTATE_3D and view3d:
            prev_view = uidoc.ActiveView
            if view3d.IsTemplate:
                print("3D tagging aborted: view is a template")
            elif isinstance(view3d, View3D) and view3d.IsPerspective:
                print("3D tagging aborted: perspective 3D cannot be tagged")
            else:
                try:
                    uidoc.RequestViewChange(view3d)
                    ui_yield(250)
                    uidoc.ActiveView = view3d
                    ui_yield(250)
                except Exception as ex:
                    print("WARNING: Could not activate 3D view: {}".format(ex))

                try:
                    with Tx(doc, "Prefab - Place 3D Tags (post-commit)"):
                        require_modifiable(
                            doc, "Prefab - Place 3D Tags (post-commit tx start)"
                        )
                        fresh_scanned_elems = []
                        invalid_skipped = 0
                        print(
                            "3D TAGGING -> scanned ids:",
                            [eid.IntegerValue for eid in scanned_ids],
                        )
                        for eid in scanned_ids:
                            elem = doc.GetElement(eid)
                            if elem and elem.IsValidObject:
                                fresh_scanned_elems.append(elem)
                            else:
                                invalid_skipped += 1
                        print(
                            "3D TAGGING -> fresh valid elems:", len(fresh_scanned_elems)
                        )
                        print("3D TAGGING -> invalid skipped:", invalid_skipped)
                        annotate_3d_view(
                            doc, view3d, fresh_scanned_elems, base_code, source_view
                        )
                except Exception as ex:
                    print("3D tagging failed post-commit: {}".format(ex))

            try:
                _activate_view(uidoc, prev_view)
                ui_yield(120)
                doc.Regenerate()
            except Exception:
                pass
    except Exception as ex:
        print("Sheet workflow failed: {}".format(ex))
        try:
            tg.RollBack()
        except Exception:
            pass
        TaskDialog.Show("Sheet Generator", "Sheet creation failed.\n\n{}".format(ex))
        return


if not proceed_with_sheet_creation:
    MessageBox.Show("Tags/text placed. Sheet creation skipped.", "Info")
else:
    mismatch_count = verify_prefab_comments_before_sheet(result, baseCode)
    if mismatch_count > 0:
        TaskDialog.Show(
            "Sheet Generator",
            "Prefab Comments verification failed before sheet creation. Mismatches: {}".format(
                mismatch_count
            ),
        )
        sys.exit("Prefab Comments verification failed")
    run_sheet_creation_workflow(
        code_norm,
        scanned_ids,
        scanned_elems,
        region_min,
        region_max,
        boundary_source_view,
    )
