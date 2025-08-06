import random
import json
import os
from flask import flash
from datetime import datetime, timedelta
import calendar
# The correct import for the Google AI library
import google.generativeai as genai

# Comprehensive and accurate scheduling rules for AI validation
SCHEDULING_RULES_TEXT = """
SHIFT SCHEDULING RULES FOR VALIDATION:

RULE 1 - TIERED SHIFT STABILITY (Seniority Privilege):
- Hierarchy Level 1 (most senior): Can stay on same shift for up to 3 consecutive months
- Hierarchy Level 2 (middle): Can stay on same shift for up to 2 consecutive months  
- Hierarchy Level 3+ (junior): Must rotate shifts every month (no stability)
- Example: If a Level 1 employee is on Morning shift in Month 1, they can stay on Morning for Months 2 and 3, but must move to a different shift in Month 4

RULE 2 - FLOATER EXEMPTION:
- Hierarchy Level 1 employees (highest seniority) CANNOT be assigned as floaters
- Only Level 2 and below employees can be floaters
- Floaters are backup staff who cover for absent team members

RULE 3 - FAIR FLOATER ROTATION:
- No employee can be a floater in consecutive months
- If Employee X is a floater in Month 1, they must be assigned to fixed staff in Month 2
- Floater duty should rotate among eligible employees (Level 2+)

RULE 4 - MANDATORY SHIFT ROTATION FOR JUNIORS:
- Level 3+ employees must work a different shift each month
- Example: If a Level 3 employee works Morning in Month 1, they cannot work Morning in Month 2

RULE 5 - MIXED-HIERARCHY COMPOSITION:
- Each shift team should contain employees from different hierarchy levels when possible
- Avoid concentrating all senior or all junior employees on one shift
- Promotes knowledge transfer and balanced teams

RULE 6 - DYNAMIC TEAM COMPOSITION:
- The exact combination of people working together should vary monthly
- Prevents formation of static groups and encourages cross-training

RULE 7 - FLOATER CALCULATION:
- Total floaters = Total team members - (Number of shifts × People per shift)
- If result is 0 or negative, no floaters are assigned
- Each floater is designated to back up a specific shift

VALIDATION CHECKS:
1. Verify Level 1 employees are never in floater positions
2. Check that no employee is floater in consecutive months  
3. Confirm Level 3+ employees have different shifts in consecutive months
4. Ensure each shift has mixed hierarchy levels when team size permits
5. Validate floater count calculation and assignment logic
"""

def generate_monthly_assignments(team, months):
    """
    Generates a rule-compliant monthly schedule with accurate state tracking.
    """
    # --- 1. SETUP AND CONFIGURATION ---
    SHIFT_DESIRABILITY_ORDER = ['Morning', 'Afternoon', 'Evening', 'Night', 'Early Morning']
    
    all_employees = sorted(
        [m.employee for m in team.members],
        key=lambda e: e.designation.hierarchy_level
    )
    if not all_employees:
        flash("No employees in this team.", "danger")
        return {}

    # Group employees by hierarchy level
    hierarchy_groups = {}
    for emp in all_employees:
        level = emp.designation.hierarchy_level
        if level not in hierarchy_groups:
            hierarchy_groups[level] = []
        hierarchy_groups[level].append(emp)

    # Determine stability configuration based on hierarchy levels
    distinct_hierarchy_levels = sorted(hierarchy_groups.keys())
    STABILITY_CONFIG = {}
    
    # Assign stability based on hierarchy level numbers (lower = more senior)
    for i, level in enumerate(distinct_hierarchy_levels):
        if i == 0:  # Most senior (lowest number)
            STABILITY_CONFIG[level] = 3
        elif i == 1:  # Second most senior
            STABILITY_CONFIG[level] = 2
        else:  # All others are junior (1 month stability = must rotate)
            STABILITY_CONFIG[level] = 1

    top_hierarchy_level = distinct_hierarchy_levels[0]
    senior_staff_exempt_from_floater = hierarchy_groups.get(top_hierarchy_level, [])

    # Team configuration
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

    # --- 2. COMPREHENSIVE STATE TRACKING ---
    all_months_assignments = {}
    employee_states = {emp.id: {
        'months_since_floater': 999,  # Start high so everyone is eligible initially
        'last_shift': None,
        'months_on_current_shift': 0,
        'current_shift': None,
        'hierarchy_level': emp.designation.hierarchy_level,
        'name': emp.name,
        'designation_title': emp.designation.title,
        'was_floater_last_month': False
    } for emp in all_employees}

    today = datetime.today()
    start_date = today.replace(day=1)

    # --- 3. MAIN MONTHLY LOOP ---
    for month_index in range(months):
        current_year = start_date.year + (start_date.month + month_index - 1) // 12
        current_month = (start_date.month + month_index - 1) % 12 + 1
        month_name = datetime(current_year, current_month, 1).strftime('%B %Y')
        
        # --- 4. FLOATER ASSIGNMENT (Rules 2 & 3) ---
        num_floaters = max(0, len(all_employees) - required_for_fixed)
        active_floaters = []
        
        if num_floaters > 0:
            # Rule 2: Exclude top hierarchy from floater duty
            floater_candidates = [e for e in all_employees 
                                if e.designation.hierarchy_level != top_hierarchy_level]
            
            # Rule 3: Exclude anyone who was floater last month
            eligible_floaters = [e for e in floater_candidates 
                               if not employee_states[e.id]['was_floater_last_month']]
            
            # If we don't have enough eligible candidates, include some from last month
            if len(eligible_floaters) < num_floaters:
                additional_candidates = [e for e in floater_candidates 
                                       if e not in eligible_floaters]
                eligible_floaters.extend(additional_candidates[:num_floaters - len(eligible_floaters)])
            
            # Sort by months since last floater duty, then by hierarchy
            eligible_floaters.sort(key=lambda e: (
                -employee_states[e.id]['months_since_floater'],
                e.designation.hierarchy_level
            ))
            
            active_floaters = eligible_floaters[:num_floaters]
        
        # Update floater states
        for emp in all_employees:
            employee_states[emp.id]['was_floater_last_month'] = (emp in active_floaters)
            if emp in active_floaters:
                employee_states[emp.id]['months_since_floater'] = 0
            else:
                employee_states[emp.id]['months_since_floater'] += 1

        # Distribute floaters across shifts
        monthly_floater_map = {shift: [] for shift in desirable_shifts}
        for i, floater in enumerate(active_floaters):
            shift_index = i % num_shifts
            monthly_floater_map[desirable_shifts[shift_index]].append(floater)

        # --- 5. FIXED STAFF ASSIGNMENT ---
        fixed_staff_pool = [e for e in all_employees if e not in active_floaters]
        
        # Initialize shift teams
        shift_teams = {shift: [] for shift in desirable_shifts}
        
        # Process employees by hierarchy level for balanced distribution
        for level in sorted(hierarchy_groups.keys()):
            level_employees = [e for e in fixed_staff_pool 
                             if e.designation.hierarchy_level == level]
            
            # Shuffle for dynamic team composition (Rule 6)
            random.shuffle(level_employees)
            
            for emp in level_employees:
                stability_months = STABILITY_CONFIG.get(level, 1)
                emp_state = employee_states[emp.id]
                
                # Determine available shifts for this employee
                available_shifts = list(desirable_shifts)
                
                # Rule 1 & 4: Handle stability and rotation requirements
                if stability_months == 1:  # Junior employees must rotate
                    if emp_state['last_shift'] and emp_state['last_shift'] in available_shifts:
                        available_shifts.remove(emp_state['last_shift'])
                elif emp_state['months_on_current_shift'] >= stability_months:
                    # Senior employee has exceeded stability period, must rotate
                    if emp_state['current_shift'] and emp_state['current_shift'] in available_shifts:
                        available_shifts.remove(emp_state['current_shift'])
                elif emp_state['current_shift'] and emp_state['current_shift'] in available_shifts:
                    # Senior employee within stability period, prefer current shift
                    available_shifts = [emp_state['current_shift']]
                
                # Find best shift assignment
                if available_shifts:
                    # Choose shift with least people for load balancing
                    best_shift = min(available_shifts, 
                                   key=lambda s: len(shift_teams[s]))
                else:
                    # Fallback: assign to least populated shift
                    best_shift = min(desirable_shifts, 
                                   key=lambda s: len(shift_teams[s]))
                
                shift_teams[best_shift].append(emp)
                
                # Update employee state tracking
                if emp_state['current_shift'] == best_shift:
                    emp_state['months_on_current_shift'] += 1
                else:
                    emp_state['last_shift'] = emp_state['current_shift']
                    emp_state['current_shift'] = best_shift
                    emp_state['months_on_current_shift'] = 1

        # --- 6. BUILD FINAL ASSIGNMENTS ---
        final_assignments_for_month = {}
        for shift_name in desirable_shifts:
            final_assignments_for_month[shift_name] = {
                'assigned_staff': [
                    {'name': emp.name, 'designation': emp.designation.title} 
                    for emp in shift_teams.get(shift_name, [])
                ],
                'floaters': [
                    {'name': f.name, 'designation': f.designation.title} 
                    for f in monthly_floater_map.get(shift_name, [])
                ]
            }
        
        all_months_assignments[month_name] = final_assignments_for_month

    return all_months_assignments


def validate_schedule_with_ai(schedule_data, rules_text, api_key):
    """
    Sends the generated schedule and rules to the Gemini API to check for violations.
    """
    prompt = f"""
    You are a schedule validation expert. Analyze the provided schedule against the given rules.
    
    CRITICAL INSTRUCTIONS:
    1. Examine each month's schedule carefully
    2. Track employee assignments across months to check for violations
    3. Respond ONLY with valid JSON in this exact format:
    {{
        "is_valid": true/false,
        "violations": ["specific violation with employee name and details", ...]
    }}
    
    RULES TO VALIDATE:
    {rules_text}

    SCHEDULE DATA TO ANALYZE:
    {schedule_data}
    
    VALIDATION PROCESS:
    1. Check Rule 1: Verify senior employees don't exceed stability limits
    2. Check Rule 2: Ensure Level 1 employees are never floaters
    3. Check Rule 3: Confirm no consecutive floater assignments
    4. Check Rule 4: Verify junior employees rotate shifts monthly
    5. Check Rule 5: Look for mixed hierarchy in shift teams
    6. Check Rule 7: Validate floater calculations
    
    For each violation found, specify:
    - Which rule was violated
    - Which employee(s) involved
    - In which month(s)
    - What the violation is exactly
    
    If no violations are found, return: {{"is_valid": true, "violations": []}}
    """
    
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        generation_config = genai.types.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1  # Lower temperature for more consistent validation
        )
        response = model.generate_content(prompt, generation_config=generation_config)
        result = json.loads(response.text)
        
        # Ensure proper format
        if 'is_valid' not in result:
            result['is_valid'] = len(result.get('violations', [])) == 0
        if 'violations' not in result:
            result['violations'] = []
            
        return result
    except Exception as e:
        error_message = f"AI Validation Error: {str(e)}"
        return {"is_valid": False, "violations": [error_message]}


def fix_schedule_with_ai(broken_schedule_data, violations_list, rules_text, api_key):
    """
    Uses AI to fix a broken schedule by addressing specific violations.
    """
    prompt = f"""
    You are a schedule correction expert. You must fix the provided schedule to resolve all violations.
    
    CRITICAL REQUIREMENTS:
    1. Maintain the exact same JSON structure as the input
    2. Keep the same months and shift names
    3. Only reassign employees to different positions to fix violations
    4. Ensure all rules are followed in the corrected schedule
    5. Respond with ONLY the corrected JSON schedule, no other text
    
    RULES TO FOLLOW:
    {rules_text}
    
    CURRENT BROKEN SCHEDULE:
    {broken_schedule_data}
    
    SPECIFIC VIOLATIONS TO FIX:
    {json.dumps(violations_list, indent=2)}
    
    CORRECTION STRATEGY:
    - For Rule 1 violations: Move senior employees who exceeded stability to different shifts
    - For Rule 2 violations: Move Level 1 employees from floater to fixed positions
    - For Rule 3 violations: Swap floaters with fixed staff to break consecutive assignments
    - For Rule 4 violations: Change junior employees' shifts to ensure monthly rotation
    - For Rule 5 violations: Redistribute employees to achieve better hierarchy balance
    
    Return the corrected schedule as valid JSON with the same structure:
    """

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        generation_config = genai.types.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.2  # Low temperature for consistent corrections
        )
        response = model.generate_content(prompt, generation_config=generation_config)
        corrected_schedule = json.loads(response.text)
        
        # Validate that the corrected schedule has the expected structure
        if not isinstance(corrected_schedule, dict):
            raise ValueError("AI returned invalid schedule format")
            
        return corrected_schedule, True
    except Exception as e:
        error_message = f"AI Correction Error: {str(e)}"
        return {"error": error_message}, False
