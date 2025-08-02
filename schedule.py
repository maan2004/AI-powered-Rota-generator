import random
import json
import os
from flask import flash
from datetime import datetime, timedelta
import calendar
# The correct import for the Google AI library
import google.generativeai as genai

# This is the text block containing the rules for the AI validator.
SCHEDULING_RULES_TEXT = """
1. Tiered Stability: The top hierarchy in the team gets 3 months of stability, the second highest gets 2 months, and all lower hierarchies must rotate shifts monthly.
2. Floater Exemption: The highest hierarchy employees in the team cannot be assigned as floaters.
3. Fair Floater Rotation: No employee can be a floater for two consecutive months.
4. Guaranteed Shift Rotation: All non-stable groups must be assigned a different shift than they had last month.
5. Mixed-Hierarchy Teams: Each fixed-shift team should be a balanced mix of different seniority levels.
6. Dynamic Teams: The specific combination of people in the junior and mid-tier groups should be shuffled monthly.
"""

def generate_monthly_assignments(team, months):
    """
    Generates a definitive, stateless, rule-based monthly schedule. The state is now managed
    externally by the SavedSchedule table.
    """
    # --- 1. RIGID RULE CONFIGURATION & INITIAL SETUP ---
    SHIFT_DESIRABILITY_ORDER = ['Morning', 'Afternoon', 'Evening', 'Night', 'Early Morning']
    
    all_employees = sorted(
        [m.employee for m in team.members],
        key=lambda e: e.designation.hierarchy_level
    )
    if not all_employees:
        flash("No employees in this team.", "danger")
        return {}

    # Dynamically determine stability perks and hierarchy tiers based on the team's structure.
    distinct_hierarchy_levels = sorted(list(set(e.designation.hierarchy_level for e in all_employees)))
    STABILITY_CONFIG = {}
    if len(distinct_hierarchy_levels) >= 2:
        STABILITY_CONFIG[distinct_hierarchy_levels[0]] = 3
        STABILITY_CONFIG[distinct_hierarchy_levels[1]] = 2
    elif len(distinct_hierarchy_levels) == 1:
        STABILITY_CONFIG[distinct_hierarchy_levels[0]] = 2
    
    top_hierarchy_level = distinct_hierarchy_levels[0]
    senior_staff_exempt_from_floater = [e for e in all_employees if e.designation.hierarchy_level == top_hierarchy_level]

    people_per_shift = team.people_per_shift
    team_shifts_map = {
        '3-shift': ['Morning', 'Afternoon', 'Night'], 
        '4-shift': ['Morning', 'Afternoon', 'Evening', 'Night'],
        '5-shift': ['Early Morning', 'Morning', 'Afternoon', 'Evening', 'Night']
    }
    shifts_in_template = team_shifts_map.get(team.shift_template, [])
    desirable_shifts = [s for s in SHIFT_DESIRABILITY_ORDER if s in shifts_in_template]
    num_shifts = len(desirable_shifts)
    
    if num_shifts == 0:
        flash(f"Team '{team.name}' has an invalid shift template configured.", "danger")
        return {}
        
    required_for_fixed = num_shifts * people_per_shift

    # --- DYNAMIC STATE TRACKING (In-Memory for a single generation) ---
    all_months_assignments = {}
    employee_states = {emp.id: {'months_since_floater': 0} for emp in all_employees}
    group_states = {} # This will be reset for each generation run

    today = datetime.today()
    start_date = today.replace(day=1)

    # --- 2. MAIN MONTHLY LOOP ---
    for month_index in range(months):
        current_year = start_date.year + (start_date.month + month_index - 1) // 12
        current_month = (start_date.month + month_index - 1) % 12 + 1
        month_name = datetime(current_year, current_month, 1).strftime('%B %Y')
        
        # --- 3. DYNAMIC ROLE ASSIGNMENT ---
        num_floaters = len(all_employees) - required_for_fixed
        active_floaters = []
        if num_floaters > 0:
            floater_candidate_pool = [e for e in all_employees if e not in senior_staff_exempt_from_floater]
            candidates = sorted(floater_candidate_pool, key=lambda e: (-employee_states[e.id]['months_since_floater'], e.designation.hierarchy_level))
            active_floaters = candidates[:num_floaters]
        
        fixed_staff_pool = [e for e in all_employees if e not in active_floaters]
        
        for emp in all_employees: employee_states[emp.id]['months_since_floater'] += 1
        for floater in active_floaters: employee_states[floater.id]['months_since_floater'] = 0

        monthly_floater_map = {shift: [] for shift in desirable_shifts}
        if num_shifts > 0:
            for i, floater in enumerate(active_floaters):
                monthly_floater_map[desirable_shifts[i % num_shifts]].append(floater)

        # --- 4. MIXED-HIERARCHY GROUP FORMATION ---
        fixed_groups = [[] for _ in range(num_shifts)]
        if fixed_staff_pool and num_shifts > 0:
            for i, emp in enumerate(fixed_staff_pool):
                fixed_groups[i % num_shifts].append(emp)
        
        # --- 5. GUARANTEED ROTATION SHIFT ASSIGNMENT ---
        monthly_shift_map = {}
        available_shifts = list(desirable_shifts)
        groups_to_rotate = []
        
        for group in fixed_groups:
            if not group: continue
            group_id = tuple(sorted(e.id for e in group))
            state = group_states.get(group_id, {'shift': None, 'consecutive_months': 0})
            
            most_senior_level_in_group = min(e.designation.hierarchy_level for e in group)
            stability_months = STABILITY_CONFIG.get(most_senior_level_in_group, 1)
            
            if state['consecutive_months'] < stability_months and state['shift'] in available_shifts:
                monthly_shift_map[state['shift']] = group
                available_shifts.remove(state['shift'])
                group_states[group_id] = {'shift': state['shift'], 'consecutive_months': state['consecutive_months'] + 1}
            else:
                groups_to_rotate.append(group)

        random.shuffle(groups_to_rotate)
        for group in groups_to_rotate:
            group_id = tuple(sorted(e.id for e in group))
            last_shift = group_states.get(group_id, {}).get('shift')
            
            options = [s for s in available_shifts if s != last_shift]
            new_shift = random.choice(options) if options else (available_shifts[0] if available_shifts else None)
            
            if new_shift:
                monthly_shift_map[new_shift] = group
                available_shifts.remove(new_shift)
                group_states[group_id] = {'shift': new_shift, 'consecutive_months': 1}

        # --- 6. BUILD THE FINAL DICTIONARY FOR THE TEMPLATE ---
        final_assignments_for_month = {}
        for shift_name in desirable_shifts:
            final_assignments_for_month[shift_name] = {
                'assigned_staff': [{'name': emp.name, 'designation': emp.designation.title} for emp in monthly_shift_map.get(shift_name, [])],
                'floaters': [{'name': f.name, 'designation': f.designation.title} for f in monthly_floater_map.get(shift_name, [])]
            }
        
        all_months_assignments[month_name] = final_assignments_for_month

    return all_months_assignments


def validate_schedule_with_ai(schedule_data, rules_text, api_key):
    """
    Sends the generated schedule and rules to the Gemini API to check for violations.
    """
    prompt = f"""
    You are a schedule validation expert. Your task is to check if the provided schedule violates any of the given rules.
    Respond ONLY with a JSON object having a key as rule no. which is violated and along with it who violated it and how.

    RULES:
    {rules_text}

    SCHEDULE DATA TO VALIDATE:
    {schedule_data}
    """
    
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        generation_config = genai.types.GenerationConfig(
            response_mime_type="application/json"
        )
        response = model.generate_content(prompt, generation_config=generation_config)
        return json.loads(response.text)
    except Exception as e:
        error_message = f"An error occurred with the AI Validator: {str(e)}"
        return {"is_valid": False, "violations": [error_message]}


def fix_schedule_with_ai(broken_schedule_data, violations_list, rules_text, api_key):
    """
    Sends a broken schedule and its violations to the AI and asks for a corrected version.
    """
    prompt = f"""
    You are a schedule correction expert. Your task is to fix a broken schedule that violates several rules.
    Here are the rules, the broken schedule, and the specific violations that were found.
    Your goal is to produce a new, corrected schedule that fixes all the violations.
    Respond ONLY with a JSON object containing the corrected schedule in the exact same format as the input. Do not add any other text.

    RULES TO FOLLOW:
    {rules_text}

    BROKEN SCHEDULE DATA:
    {broken_schedule_data}

    VIOLATIONS TO FIX:
    - {"- ".join(violations_list)}

    CORRECTED JSON SCHEDULE:
    """

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        generation_config = genai.types.GenerationConfig(
            response_mime_type="application/json"
        )
        response = model.generate_content(prompt, generation_config=generation_config)
        return json.loads(response.text), True
    except Exception as e:
        error_message = f"An error occurred while attempting to fix the schedule with AI: {str(e)}"
        return {"error": error_message}, False
