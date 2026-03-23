# Thiscode calculate Quantity of Phase in Equilibrim for series of Composition
from tc_python import *
import pandas as pd
import numpy as np

# ==================================================
# SETTINGS
# ==================================================
DATABASE = "TCAL10"
ELEMENTS = ["AL", "SI", "FE", "CU", "MN", "MG", "CR"]

INPUT_FILE = "6000.csv"
OUTPUT_FILE = "phase_quantities_vs_temperature.xlsx"

T_START_C = 700
T_END_C = 25
T_STEP_C = 5

EXCLUDED_PHASES = {"GAS", "IONIC_LIQ", "LIQUID"}
CHEM_COLS = ["Cu", "Fe", "Mg", "Mn", "Si", "Cr"]


# ==================================================
# HELPERS
# ==================================================
def build_composition_from_row(row):
    return {
        "CU": float(row["Cu"]) / 100.0,
        "FE": float(row["Fe"]) / 100.0,
        "MG": float(row["Mg"]) / 100.0,
        "MN": float(row["Mn"]) / 100.0,
        "SI": float(row["Si"]) / 100.0,
        "CR": float(row["Cr"]) / 100.0
    }


def set_composition_conditions(calculator, composition_dict):
    for el, val in composition_dict.items():
        calculator.set_condition(
            ThermodynamicQuantity.mass_fraction_of_a_component(el),
            val
        )
    return calculator


def safe_phase_volume_fraction(result, phase_name):
    try:
        value = result.get_value_of(
            ThermodynamicQuantity.volume_fraction_of_a_phase(phase_name)
        )
        if value is None or np.isnan(value):
            return 0.0
        return max(0.0, float(value))
    except Exception:
        return 0.0


def get_present_phases_from_result(result):
    candidate_method_names = [
        "get_stable_phases",
        "get_present_phases",
        "get_phases",
        "stable_phases",
        "present_phases",
        "phases"
    ]

    for name in candidate_method_names:
        if hasattr(result, name):
            attr = getattr(result, name)
            try:
                value = attr() if callable(attr) else attr
                if value is not None:
                    phases = [str(p) for p in list(value)]
                    phases = [p for p in phases if p and p not in EXCLUDED_PHASES]
                    if phases:
                        return sorted(set(phases))
            except Exception:
                pass

    return []


# ==================================================
# READ / CLEAN INPUT
# ==================================================
input_df = pd.read_csv(INPUT_FILE)

if "Index" not in input_df.columns:
    input_df.insert(0, "Index", range(1, len(input_df) + 1))

for col in CHEM_COLS:
    input_df[col] = pd.to_numeric(input_df[col], errors="coerce")

bad_rows = input_df[input_df[CHEM_COLS].isna().any(axis=1)].copy()
input_df = input_df.dropna(subset=CHEM_COLS).copy()

print("Valid alloys loaded:", len(input_df))

if len(bad_rows) > 0:
    print("Rows skipped because of missing/non-numeric chemistry:")
    print(bad_rows[["Index"] + CHEM_COLS])


# ==================================================
# MAIN CALCULATION
# ==================================================
scan_rows = []
all_detected_phases = set()
error_rows = []

with TCPython() as session:
    system = session.select_database_and_elements(DATABASE, ELEMENTS).get_system()

    for _, row in input_df.iterrows():
        alloy_id = row["Index"]
        composition = build_composition_from_row(row)

        sum_solutes = sum(composition.values())
        if sum_solutes >= 1.0:
            error_rows.append({
                "Alloy_ID": alloy_id,
                "Temperature_C": None,
                "Error": f"Sum of solute mass fractions >= 1.0 ({sum_solutes:.6f})"
            })
            continue

        print(f"Running alloy {alloy_id} ...")

        for T_C in np.arange(T_START_C, T_END_C - T_STEP_C, -T_STEP_C):
            T_K = T_C + 273.15

            try:
                calculator = system.with_single_equilibrium_calculation()
                calculator.set_condition("T", T_K)
                calculator = set_composition_conditions(calculator, composition)
                result = calculator.calculate()

            except Exception as e:
                error_rows.append({
                    "Alloy_ID": alloy_id,
                    "Temperature_C": T_C,
                    "Error": str(e)
                })
                continue

            present_phases = get_present_phases_from_result(result)

            for ph in present_phases:
                all_detected_phases.add(ph)

            scan_row = {
                "Alloy_ID": alloy_id,
                "Temperature_C": T_C
            }

            for ph in present_phases:
                vf = safe_phase_volume_fraction(result, ph)
                scan_row[ph] = vf * 100.0

            scan_rows.append(scan_row)


# ==================================================
# BUILD OUTPUT
# ==================================================
df_scan = pd.DataFrame(scan_rows)
all_detected_phases = sorted(all_detected_phases)

for ph in all_detected_phases:
    if ph not in df_scan.columns:
        df_scan[ph] = 0.0

if len(df_scan) > 0 and all_detected_phases:
    df_scan[all_detected_phases] = df_scan[all_detected_phases].fillna(0.0)

phase_cols_nonzero = []
for ph in all_detected_phases:
    if len(df_scan) > 0 and (df_scan[ph] > 0).any():
        phase_cols_nonzero.append(ph)

if len(df_scan) > 0:
    df_scan = df_scan[["Alloy_ID", "Temperature_C"] + phase_cols_nonzero]

df_errors = pd.DataFrame(error_rows)
df_bad_rows = bad_rows.copy()


# ==================================================
# EXPORT
# ==================================================
with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
    if len(df_scan) > 0:
        df_scan.to_excel(writer, sheet_name="Phase_vs_Temperature", index=False)
    if len(df_errors) > 0:
        df_errors.to_excel(writer, sheet_name="Errors", index=False)
    if len(df_bad_rows) > 0:
        df_bad_rows.to_excel(writer, sheet_name="Bad_Input_Rows", index=False)

print(f"Done. Results exported to: {OUTPUT_FILE}")
