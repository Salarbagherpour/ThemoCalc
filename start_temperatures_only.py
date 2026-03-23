# Start Temperature Equilibrium
from tc_python import *
import pandas as pd
import numpy as np

# ==================================================
# SETTINGS
# ==================================================
DATABASE = "TCAL10"
ELEMENTS = ["AL", "SI", "FE", "CU", "MN", "MG", "CR"]

INPUT_FILE = "6000.csv"
OUTPUT_FILE = "start_temperatures_only.xlsx"

T_START_C = 700
T_END_C = 25
T_STEP_C = 5

PHASE_THRESHOLD = 1e-6
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
start_tracker = {}
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
                key = (alloy_id, ph)
                if key not in start_tracker:
                    vf = safe_phase_volume_fraction(result, ph)
                    if vf > PHASE_THRESHOLD:
                        start_tracker[key] = T_C


# ==================================================
# BUILD OUTPUT
# ==================================================
all_detected_phases = sorted(all_detected_phases)

start_rows = []
for _, row in input_df.iterrows():
    alloy_id = row["Index"]
    out = {"Alloy_ID": alloy_id}
    for ph in all_detected_phases:
        out[ph] = start_tracker.get((alloy_id, ph), None)
    start_rows.append(out)

df_start = pd.DataFrame(start_rows)

if len(df_start) > 0:
    phase_cols_nonempty = [ph for ph in all_detected_phases if df_start[ph].notna().any()]
    df_start = df_start[["Alloy_ID"] + phase_cols_nonempty]
    for ph in phase_cols_nonempty:
        df_start[ph] = df_start[ph].round(0)

df_errors = pd.DataFrame(error_rows)
df_bad_rows = bad_rows.copy()


# ==================================================
# EXPORT
# ==================================================
with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
    if len(df_start) > 0:
        df_start.to_excel(writer, sheet_name="Start_Temperatures", index=False)
    if len(df_errors) > 0:
        df_errors.to_excel(writer, sheet_name="Errors", index=False)
    if len(df_bad_rows) > 0:
        df_bad_rows.to_excel(writer, sheet_name="Bad_Input_Rows", index=False)

print(f"Done. Results exported to: {OUTPUT_FILE}")
