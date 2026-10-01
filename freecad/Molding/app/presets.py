# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Save and load mold parameter presets as JSON.

Presets store all the dimensions and feature toggles that make up a mold
configuration but *not* the geometry-specific settings (pull direction,
parting offset, source object) that only make sense for a particular part.
This lets a user dial in their preferred wall thickness, key size, vent
layout and so on once, save it, and apply it to any future part.
"""

import json
import os

# Version tag so we can migrate old presets if the schema changes.
_PRESET_VERSION = 1


def preset_directory():
    """Return the preset storage folder, creating it if needed."""
    base = os.path.expanduser("~/.FreeCAD")
    folder = os.path.join(base, "MoldPresets")
    os.makedirs(folder, exist_ok=True)
    return folder


def list_presets():
    """Return a sorted list of available preset names (without extension)."""
    folder = preset_directory()
    names = []
    for entry in os.listdir(folder):
        if entry.endswith(".json"):
            names.append(entry[:-5])
    names.sort(key=str.lower)
    return names


def save_preset(name, data):
    """Write a preset dict to disk.

    *name* is a human readable label (the filename minus extension).
    *data* is a flat dict of parameter names to values.
    """
    payload = {
        "version": _PRESET_VERSION,
        "name": name,
        "parameters": data,
    }
    path = os.path.join(preset_directory(), name + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    return path


def load_preset(name):
    """Read a preset dict from disk.  Returns the parameter dict or None."""
    path = os.path.join(preset_directory(), name + ".json")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload.get("parameters", {})


def delete_preset(name):
    """Remove a preset file.  Silently does nothing if it does not exist."""
    path = os.path.join(preset_directory(), name + ".json")
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def collect_from_widgets(panel):
    """Gather all saveable parameters from a MoldWizardPanel.

    These are the dimensions, toggles, and combo selections that are not
    tied to a specific part.  Pull direction, parting offset, and source
    object are deliberately excluded.
    """
    data = {}

    # Shrink
    data["Shrink"] = panel.sb_shrink.value()

    # Block
    data["BlockStyle"] = panel.cb_block_style.currentText()
    data["WallThickness"] = panel.sb_wall.value()
    data["FloorThickness"] = panel.sb_floor.value()
    data["RoofThickness"] = panel.sb_roof.value()
    data["BlockFillet"] = panel.sb_fillet.value()

    # Filling method
    data["FillMethod"] = panel.cb_fill_method.currentText()
    data["OverpourPercent"] = panel.sb_overpour.value()
    data["SyringeSize"] = panel.cb_syringe.currentText()
    data["InjectionStyle"] = panel.cb_inj_style.currentText()
    data["InjectionDiameter"] = panel.sb_inj_dia.value()
    data["InjectionChannelLength"] = panel.sb_inj_len.value()
    data["PourDiameter"] = panel.sb_pour_dia.value()
    data["FunnelDiameter"] = panel.sb_funnel_dia.value()
    data["FunnelDepth"] = panel.sb_funnel_depth.value()

    # Layout (saved, though parting offset is not)
    data["Layout"] = panel.cb_layout.currentText()
    data["SecondaryAngle"] = panel.sb_secondary_angle.value()

    # Overflow gutter
    data["GutterEnabled"] = panel.grp_gutter.isChecked()
    data["GutterWidth"] = panel.sb_gutter_w.value()
    data["GutterDepth"] = panel.sb_gutter_d.value()
    data["GutterGap"] = panel.sb_gutter_gap.value()
    data["GutterSide"] = panel.cb_gutter_side.currentText()
    data["GutterReliefs"] = panel.sb_gutter_reliefs.value()

    # Vents
    data["VentsEnabled"] = panel.grp_vents.isChecked()
    data["VentCount"] = panel.sb_vent_count.value()
    data["VentShape"] = panel.cb_vent_shape.currentText()
    data["VentDirection"] = panel.cb_vent_direction.currentText()
    data["VentDiameter"] = panel.sb_vent_dia.value()
    data["VentWidth"] = panel.sb_vent_width.value()
    data["VentLength"] = panel.sb_vent_length.value()

    # Registration keys
    data["KeysEnabled"] = panel.grp_keys.isChecked()
    data["KeyDiameter"] = panel.sb_key_dia.value()
    data["KeyHeight"] = panel.sb_key_height.value()
    data["KeyClearance"] = panel.sb_key_clearance.value()
    data["KeyInset"] = panel.sb_key_inset.value()

    # Hardware (bolts)
    data["BoltsEnabled"] = panel.grp_hardware.isChecked()
    data["BoltCount"] = panel.sb_bolt_count.value()
    data["BoltSize"] = panel.cb_bolt_size.currentText()
    data["BoltInset"] = panel.sb_bolt_inset.value()
    data["BoltClearance"] = panel.sb_bolt_clearance.value()
    data["Counterbore"] = panel.chk_counterbore.isChecked()
    data["NutTrap"] = panel.chk_nut_trap.isChecked()

    # Pry slots
    data["PrySlotsEnabled"] = panel.grp_pry.isChecked()
    data["PrySlotWidth"] = panel.sb_pry_w.value()
    data["PrySlotDepth"] = panel.sb_pry_d.value()

    # Embossment
    data["EmbossEnabled"] = panel.grp_emboss.isChecked()
    data["EmbossText"] = panel.le_emboss_text.text()
    data["EmbossFontSize"] = panel.sb_emboss_size.value()
    data["EmbossDepth"] = panel.sb_emboss_depth.value()
    data["EmbossPlacement"] = panel.cb_emboss_placement.currentText()

    return data


def apply_to_widgets(panel, data):
    """Push a loaded preset dict onto a MoldWizardPanel's widgets.

    Unknown keys are silently ignored so older presets still load in newer
    versions of the addon.
    """
    def _float(key, default=0.0):
        return float(data.get(key, default))

    def _int(key, default=0):
        return int(data.get(key, default))

    def _bool(key, default=False):
        return bool(data.get(key, default))

    def _str(key, default=""):
        return str(data.get(key, default))

    # Shrink
    if "Shrink" in data:
        panel.sb_shrink.setValue(_float("Shrink"))

    # Block
    if "BlockStyle" in data:
        panel.cb_block_style.setCurrentText(_str("BlockStyle"))
    if "WallThickness" in data:
        panel.sb_wall.setValue(_float("WallThickness"))
    if "FloorThickness" in data:
        panel.sb_floor.setValue(_float("FloorThickness"))
    if "RoofThickness" in data:
        panel.sb_roof.setValue(_float("RoofThickness"))
    if "BlockFillet" in data:
        panel.sb_fillet.setValue(_float("BlockFillet"))

    # Filling method
    if "FillMethod" in data:
        panel.cb_fill_method.setCurrentText(_str("FillMethod"))
        panel._on_fill_method_changed(_str("FillMethod"))
    if "OverpourPercent" in data:
        panel.sb_overpour.setValue(_float("OverpourPercent"))
    if "SyringeSize" in data:
        panel.cb_syringe.setCurrentText(_str("SyringeSize"))
    if "InjectionStyle" in data:
        panel.cb_inj_style.setCurrentText(_str("InjectionStyle"))
    if "InjectionDiameter" in data:
        panel.sb_inj_dia.setValue(_float("InjectionDiameter"))
    if "InjectionChannelLength" in data:
        panel.sb_inj_len.setValue(_float("InjectionChannelLength"))
    if "PourDiameter" in data:
        panel.sb_pour_dia.setValue(_float("PourDiameter"))
    if "FunnelDiameter" in data:
        panel.sb_funnel_dia.setValue(_float("FunnelDiameter"))
    if "FunnelDepth" in data:
        panel.sb_funnel_depth.setValue(_float("FunnelDepth"))

    # Layout
    if "Layout" in data:
        panel.cb_layout.setCurrentText(_str("Layout"))
        panel._on_layout_changed(_str("Layout"))
    if "SecondaryAngle" in data:
        panel.sb_secondary_angle.setValue(_float("SecondaryAngle"))

    # Overflow gutter
    if "GutterEnabled" in data:
        panel.grp_gutter.setChecked(_bool("GutterEnabled"))
    if "GutterWidth" in data:
        panel.sb_gutter_w.setValue(_float("GutterWidth"))
    if "GutterDepth" in data:
        panel.sb_gutter_d.setValue(_float("GutterDepth"))
    if "GutterGap" in data:
        panel.sb_gutter_gap.setValue(_float("GutterGap"))
    if "GutterSide" in data:
        panel.cb_gutter_side.setCurrentText(_str("GutterSide"))
    if "GutterReliefs" in data:
        panel.sb_gutter_reliefs.setValue(_int("GutterReliefs"))

    # Vents
    if "VentsEnabled" in data:
        panel.grp_vents.setChecked(_bool("VentsEnabled"))
    if "VentCount" in data:
        panel.sb_vent_count.setValue(_int("VentCount"))
    if "VentShape" in data:
        panel.cb_vent_shape.setCurrentText(_str("VentShape"))
        panel._on_vent_shape_changed(_str("VentShape"))
    if "VentDirection" in data:
        panel.cb_vent_direction.setCurrentText(_str("VentDirection"))
    if "VentDiameter" in data:
        panel.sb_vent_dia.setValue(_float("VentDiameter"))
    if "VentWidth" in data:
        panel.sb_vent_width.setValue(_float("VentWidth"))
    if "VentLength" in data:
        panel.sb_vent_length.setValue(_float("VentLength"))

    # Registration keys
    if "KeysEnabled" in data:
        panel.grp_keys.setChecked(_bool("KeysEnabled"))
    if "KeyDiameter" in data:
        panel.sb_key_dia.setValue(_float("KeyDiameter"))
    if "KeyHeight" in data:
        panel.sb_key_height.setValue(_float("KeyHeight"))
    if "KeyClearance" in data:
        panel.sb_key_clearance.setValue(_float("KeyClearance"))
    if "KeyInset" in data:
        panel.sb_key_inset.setValue(_float("KeyInset"))

    # Hardware (bolts)
    if "BoltsEnabled" in data:
        panel.grp_hardware.setChecked(_bool("BoltsEnabled"))
    if "BoltCount" in data:
        panel.sb_bolt_count.setValue(_int("BoltCount"))
    if "BoltSize" in data:
        panel.cb_bolt_size.setCurrentText(_str("BoltSize"))
    if "BoltInset" in data:
        panel.sb_bolt_inset.setValue(_float("BoltInset"))
    if "BoltClearance" in data:
        panel.sb_bolt_clearance.setValue(_float("BoltClearance"))
    if "Counterbore" in data:
        panel.chk_counterbore.setChecked(_bool("Counterbore"))
    if "NutTrap" in data:
        panel.chk_nut_trap.setChecked(_bool("NutTrap"))

    # Pry slots
    if "PrySlotsEnabled" in data:
        panel.grp_pry.setChecked(_bool("PrySlotsEnabled"))
    if "PrySlotWidth" in data:
        panel.sb_pry_w.setValue(_float("PrySlotWidth"))
    if "PrySlotDepth" in data:
        panel.sb_pry_d.setValue(_float("PrySlotDepth"))

    # Embossment
    if "EmbossEnabled" in data:
        panel.grp_emboss.setChecked(_bool("EmbossEnabled"))
    if "EmbossText" in data:
        panel.le_emboss_text.setText(_str("EmbossText"))
    if "EmbossFontSize" in data:
        panel.sb_emboss_size.setValue(_float("EmbossFontSize"))
    if "EmbossDepth" in data:
        panel.sb_emboss_depth.setValue(_float("EmbossDepth"))
    if "EmbossPlacement" in data:
        panel.cb_emboss_placement.setCurrentText(_str("EmbossPlacement"))
