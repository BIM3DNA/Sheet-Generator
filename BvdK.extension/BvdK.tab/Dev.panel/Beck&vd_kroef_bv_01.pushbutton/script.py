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
       • For Pipes and Pipe Tags, the NewCode becomes: [Base].1, [Base].2, … (sorted left-to-right, bottom-to-up).
       • For Pipe Fittings, the NewCode is simply [Base].

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
    "geberit_pe": {
        "pipes": "Geberit PE leidingen - PREFAB -1",
        "fittings": "Geberit PE fittingen - PREFAB -1",
    },
    "geberit_silent": {
        "pipes": "Geberit PE Silent leidingen - PREFAB -1",
        "fittings": "Geberit PE Silent fittingen - PREFAB -1",
    },
    "wavin_tigris": {
        "pipes": "Wavin Tigris Pipe Schedule -1",
        "fittings": "Wavin Tigris Pipe Fitting Schedule",
        "flex": "Wavin Tigris Flexible Pipe Schedule -2",
    },
    "wavin_wafix_pp": {
        "pipes": "Wavin Wafix PP Pipes",
        "fittings_bom": "Wavin Wafix PP Pipe Fittings BOM -1",
        "accessory_bom": "Wavin Wafix PP Pipe Accessory BOM -1",
    },
    "wavin_wafix_pvc": {
        "pipes": "Wavin WafixPVC Pipes",
        "fittings": "Wavin WafixPVC Pipe Fittings",
    },
    "pipe_accessory": {
        "schedule": "Pipe Accessory Schedule - PREFAB -1",
    },
    "plumbing_fixture": {
        "schedule": "Plumbing Fixture Schedule - PREFAB -2",
    },
    "geberit_pluvia": {
        "trechters": "Geberit Pluvia trechters - PREFAB",
        "flex": "Geberit Pluvia flexible aansluitslang - PREFAB",
    },
    "duofix": {
        "bedieningsplaten": "Duofix bedieningsplaten - PREFAB",
        "elementen": "Duofix elementen - PREFAB -1",
    },
    "geberit_montage": {
        "schedule": "Geberit Montage - PREFAB -1",
    },
}


def normalize_schedule_name(name):
    return " ".join(
        name.lower().replace("_", " ").replace("-", " ").split()
    )


def find_schedule_view_by_name(doc, name):
    for vs in FilteredElementCollector(doc).OfClass(ViewSchedule):
        if vs.Name == name:
            return vs
    target = normalize_schedule_name(name)
    for vs in FilteredElementCollector(doc).OfClass(ViewSchedule):
        if normalize_schedule_name(vs.Name) == target:
            return vs
    return None


def detect_prefab_systems(elements):
    detected = set()
    has_pipe_accessories = False
    has_plumbing_fixtures = False
    has_flex_pipes = False
    manufacturers = set()
    product_series = set()

    for elem in elements:
        if not elem.Category:
            continue
        cat_id = elem.Category.Id.IntegerValue
        family_name = ""
        try:
            if isinstance(elem, FamilyInstance) and elem.Symbol and elem.Symbol.Family:
                family_name = elem.Symbol.Family.Name or ""
        except Exception:
            family_name = ""
        family_name_lc = family_name.lower()
        man = (get_type_param(elem, "Manufacturer") or get_inst_param(elem, "Manufacturer") or "").strip()
        prod = (get_type_param(elem, "product_series") or get_inst_param(elem, "product_series") or "").strip()
        man_lc = man.lower()
        prod_lc = prod.lower()
        if man:
            manufacturers.add(man)
        if prod:
            product_series.add(prod)

        if cat_id == int(BuiltInCategory.OST_PipeAccessory):
            has_pipe_accessories = True
        elif cat_id == int(BuiltInCategory.OST_PlumbingFixtures):
            has_plumbing_fixtures = True
        elif cat_id == int(BuiltInCategory.OST_FlexPipeCurves):
            has_flex_pipes = True

        if "wavin" in man_lc:
            if prod_lc == "tigris":
                detected.add("wavin_tigris")
            if prod_lc == "wafix pp" or "wafix" in prod_lc:
                detected.add("wavin_wafix_pp")
            if "wafix pvc" in prod_lc or "wafixpvc" in prod_lc:
                detected.add("wavin_wafix_pvc")

        if "geberit" in man_lc:
            if "silent" in family_name_lc or "silent" in prod_lc:
                detected.add("geberit_silent")
            if "pluvia" in family_name_lc or "pluvia" in prod_lc:
                detected.add("geberit_pluvia")
            if "duofix" in family_name_lc or "duofix" in prod_lc:
                detected.add("duofix")
            if "montage" in family_name_lc or "montage" in prod_lc:
                detected.add("geberit_montage")
            if "pe" in family_name_lc or prod_lc == "pe":
                detected.add("geberit_pe")

    if has_pipe_accessories:
        detected.add("pipe_accessory")
    if has_plumbing_fixtures:
        detected.add("plumbing_fixture")

    if "Wavin" in manufacturers and "Tigris" in product_series:
        detected.add("wavin_tigris")
    if "Wavin" in manufacturers and any("Wafix" in x for x in product_series):
        detected.add("wavin_wafix_pp")

    return detected, has_flex_pipes


def apply_prefab_filter_to_schedule(vs, prefab_code):
    try:
        sd = vs.Definition
    except Exception as ex:
        print("Cannot access schedule definition '{}': {}".format(vs.Name, ex))
        return

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
    for i in reversed(range(sd.GetFilterCount())):
        f = sd.GetFilter(i)
        if f.FieldId == comment_field_id:
            sd.RemoveFilter(i)
    sd.AddFilter(ScheduleFilter(comment_field_id, ScheduleFilterType.Equal, prefab_code))


def duplicate_schedules_for_systems(doc, detected_systems, prefab_code):
    created = []
    for syskey in sorted(detected_systems):
        if syskey not in SCHEDULE_TEMPLATES:
            print("No schedule template mapping for {}".format(syskey))
            continue
        for role, sched_name in SCHEDULE_TEMPLATES[syskey].items():
            src = find_schedule_view_by_name(doc, sched_name)
            if not src:
                print("Schedule template not found: '{}'".format(sched_name))
                continue

            t = Transaction(doc, "Duplicate schedule: {}".format(sched_name))
            try:
                t.Start()
                print("About to duplicate schedule: {}".format(sched_name))
                new_id = src.Duplicate(ViewDuplicateOption.Duplicate)
                new_vs = doc.GetElement(new_id)
                target_name = "{} {}".format(sched_name, prefab_code)
                final_name = target_name
                suffix = 2
                while find_schedule_view_by_name(doc, final_name):
                    final_name = "{} ({})".format(target_name, suffix)
                    suffix += 1
                new_vs.Name = final_name
                apply_prefab_filter_to_schedule(new_vs, prefab_code)
                t.Commit()
                created.append(new_vs.Id)
            except Exception as ex:
                rollback_transaction(t)
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
        self.colBend45.HeaderText = "2x45°"
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
                        row.Cells["TagStatus"].Value = "Flip 2x45°"
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

                # 2. Turn OFF 2x45°
                p_bend = elem.LookupParameter("2x45°")
                if p_bend and p_bend.StorageType == StorageType.Integer:
                    if p_bend.AsInteger() == 1:
                        debug(" -> Turning OFF 2x45°")
                        t = Transaction(doc, "Turn off 2x45°")
                        t.Start()
                        p_bend.Set(0)
                        t.Commit()
                        updated += 1
                    elif not reducer_fixed:
                        debug(" -> 2x45° already OFF")
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
                                        "⚠️ Failed to resolve vertical pipe diameter:",
                                        ex,
                                    )

                        if vertical_diam is not None:
                            try:
                                bend_param = elem.LookupParameter("2x45°")
                                if (
                                    bend_param
                                    and bend_param.StorageType == StorageType.Integer
                                ):
                                    t = Transaction(doc, "Set 2x45° for elbow")
                                    t.Start()
                                    if vertical_diam > 100:
                                        bend_param.Set(1)
                                        debug(
                                            "✅ 2x45° turned ON for:",
                                            elem.Id,
                                            "| Ø =",
                                            vertical_diam,
                                        )
                                    else:
                                        bend_param.Set(0)
                                        debug(
                                            "✅ 2x45° turned OFF for:",
                                            elem.Id,
                                            "| Ø =",
                                            vertical_diam,
                                        )
                                    t.Commit()
                                    updated += 1
                            except Exception as ex:
                                debug("❌ Failed to set 2x45° on elbow:", ex)

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
                                        "✅ Turned OFF reducer_eccentric for vertical-up multireducer:",
                                        elem.Id,
                                    )
                                    updated += 1
                        except Exception as ex:
                            debug("❌ Failed to auto-toggle reducer_eccentric:", ex)

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

                # Flip 2x45° logic (manual logic reused)
                if tag_status == "Flip 2x45°":
                    debug(">> Activating Flip 2x45° for:", name)
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

                            debug("✅ Flip 2x45° applied to:", eid)
                        except Exception as e:
                            debug("⚠️ Failed to flip 2x45° for", eid, "Error:", str(e))

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
                            debug("✅ Fixed vertical reducer for:", eid)
                    except Exception as ve:
                        debug(
                            "❌ Error fixing vertical reducer for:", eid, "Error:", ve
                        )

                # Re-read parameters from Revit
                p_warn = elem.LookupParameter("waarschuwing")
                warning_val = p_warn.AsString() if p_warn else ""
                row.Cells["Warning"].Value = warning_val

                p_bend = elem.LookupParameter("2x45°")
                if p_bend and p_bend.StorageType == StorageType.Integer:
                    bend45_val = "Yes" if p_bend.AsInteger() == 1 else "No"
                    row.Cells["Bend45"].Value = bend45_val
            except:
                continue

        MessageBox.Show(
            "✅ Reducers Fixed!\n\nUpdated: {}\nSkipped: {}".format(updated, skipped),
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

    def bulkAddRemoveTags_Click(self, sender, event):
        rows_to_process = []
        for row in self.dataGrid.Rows:
            cat = row.Cells["Category"].Value
            if cat == "Pipes":
                rows_to_process.append(row)

        for row in rows_to_process:
            val = row.Cells["TagStatus"].Value
            host_id = int(str(row.Cells["Id"].Value))
            host = doc.GetElement(ElementId(host_id))

            if val == "Add/Place Tag":
                tr = Transaction(doc, "Add Tag")
                tr.Start()
                bb = host.get_BoundingBox(uidoc.ActiveView)
                if bb:
                    ctr = XYZ(
                        (bb.Min.X + bb.Max.X) / 2.0,
                        (bb.Min.Y + bb.Max.Y) / 2.0,
                        (bb.Min.Z + bb.Max.Z) / 2.0,
                    )
                    ref = Reference(host)
                    new_tag = IndependentTag.Create(
                        doc,
                        doc.ActiveView.Id,
                        ref,
                        True,
                        TagMode.TM_ADDBY_CATEGORY,
                        TagOrientation.Horizontal,
                        ctr,
                    )
                tr.Commit()
                row.Cells["TagStatus"].Value = "Remove Tag"
                te = doc.GetElement(new_tag.Id)
                if te:
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

            elif val == "Remove Tag":
                host_eid = host.Id.IntegerValue
                deleted_id = None
                tag_elem_id = None
                for t in (
                    FilteredElementCollector(doc)
                    .OfCategory(BuiltInCategory.OST_PipeTags)
                    .WhereElementIsNotElementType()
                    .ToElements()
                ):
                    tagged = (
                        t.GetTaggedElementIds()
                        if hasattr(t, "GetTaggedElementIds")
                        else [t.TaggedElementId]
                    )
                    for rid in tagged:
                        eid = (
                            rid.HostElementId.IntegerValue
                            if hasattr(rid, "HostElementId")
                            else rid.IntegerValue
                        )
                        if eid == host_eid:
                            deleted_id = t.Id.IntegerValue
                            tag_elem_id = t.Id
                            break
                    if deleted_id:
                        break
                if deleted_id:
                    self.dataGrid.SelectionChanged -= self.on_row_selected
                    tr = Transaction(doc, "Remove Tag")
                    tr.Start()
                    doc.Delete(tag_elem_id)
                    tr.Commit()
                    row.Cells["TagStatus"].Value = "Add/Place Tag"
                    row.Cells["TagStatus"].ReadOnly = False
                    for i in range(self.dataGrid.Rows.Count):
                        r2 = self.dataGrid.Rows[i]
                        if (
                            r2.Cells["Category"].Value == "Pipe Tags"
                            and int(str(r2.Cells["Id"].Value)) == deleted_id
                        ):
                            self.dataGrid.Rows.RemoveAt(i)
                            break
                    self.dataGrid.SelectionChanged += self.on_row_selected

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
                debug("✅ Family name:", fam_name)

                if "multireducer_geb" in fam_name:
                    row.Cells["TagStatus"].Value = "Flip Reducer"
                    row.Cells["TagStatus"].ReadOnly = False

                elif "multibocht" in fam_name:
                    row.Cells["TagStatus"].Value = "Flip 2x45°"
                    row.Cells["TagStatus"].ReadOnly = False

                elif "liggend" in fam_name:
                    row.Cells["TagStatus"].Value = "Flip T-stuk"
                    row.Cells["TagStatus"].ReadOnly = False

        except Exception as ex:
            debug("⚠️ Error resolving Flip button logic:", ex)

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
        (region_min, region_max) = get_region_bounding_box(self.regionElements)
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

        # 6) Mirror pipe numbering onto pipe‐tag rows (same count)
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
                                "🔄 Flipped T-stuk (switch_excentriciteit = %s): %s"
                                % (str(not current), str(elem.Id))
                            )
                except Exception as ex:
                    debug("❌ Failed to flip T-stuk using switch_excentriciteit:", ex)

            elif cat == "Pipe Fittings" and val == "Flip 2x45°":
                try:
                    host_id = int(str(row.Cells["Id"].Value))
                    elem = doc.GetElement(ElementId(host_id))
                    if elem:
                        param = elem.LookupParameter("2x45°")
                        if param and param.StorageType == StorageType.Integer:
                            current_val = param.AsInteger()

                            t = Transaction(doc, "Toggle 2x45°")
                            t.Start()
                            param.Set(0 if current_val == 1 else 1)
                            t.Commit()

                            if current_val == 1:
                                row.Cells["Bend45"].Value = "No"
                            else:
                                row.Cells["Bend45"].Value = "Yes"

                            debug(
                                "✅ Toggled 2x45° to",
                                "OFF" if current_val == 1 else "ON",
                                "for:",
                                host_id,
                            )
                        else:
                            debug(
                                "⚠️ Parameter '2x45°' not found or not boolean:", host_id
                            )
                    else:
                        debug("⚠️ Could not get element from ID:", host_id)
                except Exception as ex:
                    debug("❌ Failed to flip 2x45°:", ex)

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
                                    "✅ Toggled reducer_eccentric to",
                                    "OFF" if current_val == 1 else "ON",
                                    "for:",
                                    host_id,
                                )
                except Exception as ex:
                    debug("❌ Failed to toggle redcuer_eccentric:", ex)

            # ----------------------
            # ADD/REMOVE TAG (Pipes)
            # ----------------------
            elif cat == "Pipes":
                host_id = int(str(row.Cells["Id"].Value))
                host = doc.GetElement(ElementId(host_id))

                if val == "Add/Place Tag":
                    tr = Transaction(doc, "Add Tag")
                    tr.Start()
                    bb = host.get_BoundingBox(uidoc.ActiveView)
                    if bb:
                        ctr = XYZ(
                            (bb.Min.X + bb.Max.X) / 2.0,
                            (bb.Min.Y + bb.Max.Y) / 2.0,
                            (bb.Min.Z + bb.Max.Z) / 2.0,
                        )
                        ref = Reference(host)
                        new_tag = IndependentTag.Create(
                            doc,
                            doc.ActiveView.Id,
                            ref,
                            True,
                            TagMode.TM_ADDBY_CATEGORY,
                            TagOrientation.Horizontal,
                            ctr,
                        )
                    tr.Commit()

                    row.Cells["TagStatus"].Value = "Remove Tag"

                    # Add new Pipe Tag row
                    te = doc.GetElement(new_tag.Id)
                    if te:
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

                elif val == "Remove Tag":
                    host_eid = host.Id.IntegerValue
                    deleted_id = None
                    tag_elem_id = None
                    for t in (
                        FilteredElementCollector(doc)
                        .OfCategory(BuiltInCategory.OST_PipeTags)
                        .WhereElementIsNotElementType()
                        .ToElements()
                    ):
                        tagged = (
                            t.GetTaggedElementIds()
                            if hasattr(t, "GetTaggedElementIds")
                            else [t.TaggedElementId]
                        )
                        for rid in tagged:
                            eid = (
                                rid.HostElementId.IntegerValue
                                if hasattr(rid, "HostElementId")
                                else rid.IntegerValue
                            )
                            if eid == host_eid:
                                deleted_id = t.Id.IntegerValue
                                tag_elem_id = t.Id
                                break
                        if deleted_id:
                            break
                    if deleted_id:
                        self.dataGrid.SelectionChanged -= self.on_row_selected
                        tr = Transaction(doc, "Remove Tag")
                        tr.Start()
                        doc.Delete(tag_elem_id)
                        tr.Commit()
                        row.Cells["TagStatus"].Value = "Add/Place Tag"
                        row.Cells["TagStatus"].ReadOnly = False
                        for i in range(self.dataGrid.Rows.Count):
                            r2 = self.dataGrid.Rows[i]
                            if (
                                r2.Cells["Category"].Value == "Pipe Tags"
                                and int(str(r2.Cells["Id"].Value)) == deleted_id
                            ):
                                self.dataGrid.Rows.RemoveAt(i)
                                break
                        self.dataGrid.SelectionChanged += self.on_row_selected

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
                    # build your dict exactly like you do for pipe‑tags below
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

            p_bend = e.LookupParameter("2x45°")
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


# ==================================================
# MAIN WORKFLOW
# ==================================================
gathered_elements = select_boundary_and_gather()
if gathered_elements is None or len(gathered_elements) == 0:
    MessageBox.Show("No elements were gathered. Operation cancelled.", "Error")
    sys.exit("Operation cancelled by the user.")

filtered_elements = filter_relevant_elements(gathered_elements)
if len(filtered_elements) == 0:
    MessageBox.Show("No relevant elements found in the selected region.", "Error")
    sys.exit("Operation cancelled by the user.")

result = show_element_editor(filtered_elements, region_elements=gathered_elements)
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
for eData in result["Elements"]:
    if eData["Category"] == "Pipe Fittings":
        eData["NewCode"] = baseCode

# --- Renumber Pipes based on region order (sorted left-to-right, bottom-to-up) ---
if not result.get("TextNotePlaced", False):
    base = code_norm

    pipe_entries = []
    for idx, eData in enumerate(result["Elements"]):
        if eData["Category"] == "Pipes":
            elem = doc.GetElement(ElementId(int(str(eData["Id"]))))
            if elem:
                bbox = elem.get_BoundingBox(uidoc.ActiveView)
                if bbox:
                    center = XYZ(
                        (bbox.Min.X + bbox.Max.X) / 2.0,
                        (bbox.Min.Y + bbox.Max.Y) / 2.0,
                        (bbox.Min.Z + bbox.Max.Z) / 2.0,
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

# --- Update the elements' "Comments" from the DataGridView ---
t = Transaction(doc, "Update Comments")
try:
    t.Start()
    for eData in result["Elements"]:
        id_val = eData.get("Id")
        try:
            eid = int(str(id_val))
        except (TypeError, ValueError):
            continue

        elem = doc.GetElement(ElementId(eid))
        if not elem:
            continue
        p = elem.LookupParameter("Comments")
        if not p or p.IsReadOnly:
            continue
        p.Set(code_norm)
    t.Commit()
except Exception:
    rollback_transaction(t)
    raise

# --- Place the text note if not already placed ---
if not result.get("TextNotePlaced", False):
    (region_min, region_max) = get_region_bounding_box(gathered_elements)
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

region_min, region_max = get_region_bounding_box(gathered_elements)
boundary_source_view = uidoc.ActiveView


def run_sheet_creation_workflow(
    base_code, gathered_elements, region_min, region_max, source_view
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

            ok = Button(Text="OK", DialogResult=DialogResult.OK, Location=Point(10, 300))
            ca = Button(
                Text="Cancel", DialogResult=DialogResult.Cancel, Location=Point(100, 300)
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
    tg = TransactionGroup(doc, "Prefab Sheet Creation")
    try:
        tg.Start()

        tx = Transaction(doc, "Create Cropped Plan View")
        try:
            tx.Start()
            new_view, route_used = duplicate_or_recreate_plan_view(
                doc, orig, duplicate_option, base_code
            )
            print("Plan view created via: {}".format(route_used))
            new_view.ViewTemplateId = ElementId.InvalidElementId
            new_view.Discipline = ViewDiscipline.Coordination
            new_view.Scale = 25

            bb = BoundingBoxXYZ()
            bb.Min = region_min
            bb.Max = region_max
            bb.Transform = orig_trans
            new_view.CropBoxActive = True
            new_view.CropBoxVisible = True
            annoParam = new_view.get_Parameter(
                BuiltInParameter.VIEWER_ANNOTATION_CROP_ACTIVE
            )
            if annoParam and not annoParam.IsReadOnly:
                annoParam.Set(1)
            new_view.CropBox = bb
            param = new_view.get_Parameter(BuiltInParameter.VIEWER_CROP_REGION_VISIBLE)
            if param and not param.IsReadOnly:
                param.Set(0)
            cat_drag_controls = doc.Settings.Categories.get_Item(
                BuiltInCategory.OST_ConnectorElem
            )
            if cat_drag_controls and new_view.CanCategoryBeHidden(cat_drag_controls.Id):
                new_view.SetCategoryHidden(cat_drag_controls.Id, True)
            tx.Commit()
        except Exception:
            rollback_transaction(tx)
            raise

        sheet = None
        tb_center = None
        for base in {base_code}:
            if base in existing_numbers:
                raise Exception("Sheet already exists: {}".format(base))

            t3 = Transaction(doc, "Create Sheet and 3D")
            try:
                t3.Start()
                sheet = ViewSheet.Create(doc, title_block.Id)
                sheet.SheetNumber = base
                sheet.Name = "Prefab " + base

                titleblock_inst = next(
                    (
                        e
                        for e in FilteredElementCollector(doc, sheet.Id)
                        .OfClass(FamilyInstance)
                        .ToElements()
                        if e.Symbol.Id == title_block.Id
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
                Viewport.Create(doc, sheet.Id, new_view.Id, tb_center)

                all3ds = FilteredElementCollector(doc).OfClass(ViewFamilyType).ToElements()
                v3d_type = next(
                    v for v in all3ds if v.ViewFamily == ViewFamily.ThreeDimensional
                )
                view3d = View3D.CreateIsometric(doc, v3d_type.Id)
                try:
                    view3d.Name = "{} - Sheet".format(base)
                except Exception as ex:
                    print("3D view naming failed for {}: {}".format(base, ex))
                view3d.Discipline = ViewDiscipline.Architectural
                view3d.Scale = 25
                section_bb = BoundingBoxXYZ()
                section_bb.Min = region_min
                section_bb.Max = region_max
                view3d.SetSectionBox(section_bb)
                view3d.IsSectionBoxActive = True
                section_box = view3d.GetSectionBox()
                if section_box:
                    section_box.Enabled = True
                Viewport.Create(
                    doc,
                    sheet.Id,
                    view3d.Id,
                    XYZ(tb_center.X + 0.25, tb_center.Y + 0.25, 0),
                )
                t3.Commit()
            except Exception:
                rollback_transaction(t3)
                raise

        detected_systems, has_flex_pipes = detect_prefab_systems(gathered_elements)
        if has_flex_pipes and "wavin_tigris" in detected_systems:
            pass
        created_schedule_ids = duplicate_schedules_for_systems(
            doc, detected_systems, base_code
        )

        place_tx = Transaction(doc, "Place Schedules on Sheet")
        try:
            place_tx.Start()
            uMin, uMax = sheet.Outline.Min.U, sheet.Outline.Max.U
            vMin, vMax = sheet.Outline.Min.V, sheet.Outline.Max.V
            w, h = (uMax - uMin), (vMax - vMin)
            x = uMin + 0.05 * w
            y_start = vMax - 0.35 * h
            spacing = 0.12 * h
            for idx, sched_id in enumerate(created_schedule_ids):
                y = y_start - idx * spacing
                ScheduleSheetInstance.Create(doc, sheet.Id, sched_id, XYZ(x, y, 0))
            place_tx.Commit()
        except Exception:
            rollback_transaction(place_tx)
            raise

        move_tx = Transaction(doc, "Center Views and Schedules on Sheet")
        try:
            move_tx.Start()
            viewports = (
                FilteredElementCollector(doc, sheet.Id).OfClass(Viewport).ToElements()
            )
            for vp in viewports:
                vp.SetBoxCenter(tb_center)
            move_tx.Commit()
        except Exception:
            rollback_transaction(move_tx)
            raise

        tg.Assimilate()
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
    run_sheet_creation_workflow(
        code_norm,
        gathered_elements,
        region_min,
        region_max,
        boundary_source_view,
    )
