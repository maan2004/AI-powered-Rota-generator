import random
import json
import os
from flask import flash
from datetime import datetime, timedelta
import calendar
# The correct import for the Google AI library
import google.generativeai as genai

# More precise and accurate scheduling rules for AI validation
SCHEDULING_RULES_TEXT = """
SHIFT SCHEDULING RULES FOR VALIDATION:

CRITICAL: You MUST be 100% certain before reporting ANY violation. Only report violations that definitively exist with concrete evidence.

RULE 1 - TIERED SHIFT STABILITY:
- Hierarchy Level 1 (most senior): Can stay on same shift for up to 3 consecutive months
- Hierarchy Level 2 (middle): Can stay on same shift for up to 2 consecutive months  
- Hierarchy Level 3+ (junior): Must rotate shifts every month
- ONLY report if you can prove an employee exceeded their exact allowed stability period
- You must track each employee's consecutive months on the same shift

RULE 2 - FLOATER EXEMPTION:
- Hierarchy Level 1 employees CANNOT be assigned as floaters
- ONLY report if you find a Level 1 employee listed in the "floaters" section
- Be very careful to identify hierarchy levels correctly

RULE 3 - FAIR FLOATER ROTATION:
- No employee can be a floater in consecutive months
- ONLY report if you see the EXACT SAME PERSON in floaters in consecutive months
- You must verify the person's name appears in floaters in consecutive months

RULE 4 - MANDATORY SHIFT ROTATION FOR JUNIORS:
- Level 3+ employees must work different shifts each month when assigned to fixed staff
- ONLY report if a Level 3+ employee works the SAME SHIFT in consecutive months as assigned_staff
- Floater assignments don't count for this rule

VALIDATION METHODOLOGY:
1. First, identify the hierarchy levels of all employees based on their designations
2. Track each employee's assignments month by month
3. For each rule, build concrete evidence before reporting
4. Double-check your findings
5. If you have ANY doubt, do NOT report the violation

RESPONSE FORMAT:
Always respond with valid JSON:
{
    "is_valid": true/false,
    "violations": ["Specific violation with employee name, months, and evidence"],
    "validation_notes": "Brief explanation of what you checked"
}
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


def validate_schedule_with_ai(schedule_data, rules_text, api_key, team_hierarchy_info=None):
    """
    Improved AI validation with actual team hierarchy information.
    """
    # Build hierarchy context from actual team data
    hierarchy_context = ""
    if team_hierarchy_info:
        hierarchy_context = f"""
ACTUAL TEAM HIERARCHY INFORMATION:
{json.dumps(team_hierarchy_info, indent=2)}

IMPORTANT: Use ONLY the above hierarchy information to determine employee levels. 
- hierarchy_level 1 = Most Senior (Level 1)
- hierarchy_level 2 = Middle (Level 2) 
- hierarchy_level 3+ = Junior (Level 3+)

DO NOT use generic designation name assumptions. Use the actual hierarchy_level numbers provided above.
"""
    
    prompt = f"""
    You are an expert schedule validator. You must be EXTREMELY careful and conservative.

    CRITICAL VALIDATION INSTRUCTIONS:
    1. Only report violations you are 100% certain about
    2. Provide concrete evidence for each violation
    3. Double-check your work before reporting anything
    4. When in doubt, do NOT report a violation
    5. Focus on actual rule violations, not preferences or optimizations

    {hierarchy_context}

    STEP-BY-STEP PROCESS:
    1. Parse the schedule structure carefully
    2. Create a tracking table for each employee across months
    3. Use the provided hierarchy information to identify employee levels
    4. Check each rule systematically with evidence
    5. Only report violations with specific employee names and months

    {rules_text}

    SCHEDULE TO ANALYZE:
    {schedule_data}

    VALIDATION CHECKLIST:
    □ Rule 1: Check if any employee exceeded their stability period
    □ Rule 2: Check if any Level 1 employee is in floaters
    □ Rule 3: Check if same person is floater in consecutive months
    □ Rule 4: Check if Level 3+ employee has same shift in consecutive months

    Return JSON with this exact format:
    {{
        "is_valid": true/false,
        "violations": ["Specific violation with evidence"],
        "validation_notes": "Summary of what was checked"
    }}

    If NO violations are found, return: {{"is_valid": true, "violations": [], "validation_notes": "All rules passed validation"}}
    """
    
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        generation_config = genai.types.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.0  # Deterministic for consistent validation
        )
        response = model.generate_content(prompt, generation_config=generation_config)
        result = json.loads(response.text)
        
        # Ensure proper format
        if 'is_valid' not in result:
            result['is_valid'] = len(result.get('violations', [])) == 0
        if 'violations' not in result:
            result['violations'] = []
        if 'validation_notes' not in result:
            result['validation_notes'] = "Validation completed"
            
        return result
    except Exception as e:
        error_message = f"AI Validation Error: {str(e)}"
        return {
            "is_valid": False, 
            "violations": [error_message],
            "validation_notes": "Error occurred during validation"
        }


def fix_schedule_with_ai(broken_schedule_data, violations_list, rules_text, api_key, team_hierarchy_info=None):
    """
    More targeted AI fixing with actual team hierarchy information.
    """
    # Build hierarchy context from actual team data
    hierarchy_context = ""
    if team_hierarchy_info:
        hierarchy_context = f"""
ACTUAL TEAM HIERARCHY INFORMATION:
{json.dumps(team_hierarchy_info, indent=2)}

IMPORTANT: Use ONLY the above hierarchy information to determine employee levels.
- hierarchy_level 1 = Most Senior (Level 1) 
- hierarchy_level 2 = Middle (Level 2)
- hierarchy_level 3+ = Junior (Level 3+)
"""
    
    prompt = f"""
    You are a schedule correction expert. Your task is to fix ONLY the specific violations listed below.

    CORRECTION PRINCIPLES:
    1. Make MINIMAL changes - only fix what's broken
    2. Maintain the exact same JSON structure
    3. Keep all month names and shift names identical
    4. Preserve as much of the original schedule as possible
    5. Ensure corrections don't create new violations

    {hierarchy_context}

    CURRENT SCHEDULE:
    {broken_schedule_data}

    SPECIFIC VIOLATIONS TO FIX:
    {json.dumps(violations_list, indent=2)}

    RULES REFERENCE:
    {rules_text}

    CORRECTION STRATEGY:
    - Identify the exact employees and months mentioned in violations
    - Use the provided hierarchy information to understand employee levels
    - Make surgical changes (swaps, reassignments) to fix these specific issues
    - Verify your changes don't introduce new problems
    - Keep team balance and coverage intact

    IMPORTANT: Return ONLY the corrected schedule JSON with no additional text or explanations.
    """

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        generation_config = genai.types.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1  # Low temperature for consistent corrections
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


def validate_schedule_programmatically(schedule_data, team_hierarchy_info=None):
    """
    Programmatic validation using actual team hierarchy information.
    """
    violations = []
    
    try:
        schedule = json.loads(schedule_data) if isinstance(schedule_data, str) else schedule_data
        
        # Build hierarchy mapping from actual team data
        hierarchy_mapping = {}
        if team_hierarchy_info:
            for emp_info in team_hierarchy_info:
                hierarchy_mapping[emp_info['name']] = emp_info['hierarchy_level']
        
        # Track employee assignments across months
        employee_tracking = {}
        month_names = list(schedule.keys())
        
        # Build tracking data
        for month_name in month_names:
            month_data = schedule[month_name]
            
            for shift_name, shift_data in month_data.items():
                # Track assigned staff
                for staff in shift_data.get('assigned_staff', []):
                    emp_name = staff['name']
                    if emp_name not in employee_tracking:
                        employee_tracking[emp_name] = {}
                    employee_tracking[emp_name][month_name] = {
                        'role': 'assigned_staff',
                        'shift': shift_name,
                        'designation': staff['designation']
                    }
                
                # Track floaters
                for floater in shift_data.get('floaters', []):
                    emp_name = floater['name']
                    if emp_name not in employee_tracking:
                        employee_tracking[emp_name] = {}
                    employee_tracking[emp_name][month_name] = {
                        'role': 'floater',
                        'shift': shift_name,
                        'designation': floater['designation']
                    }
        
        # Rule 2: Check senior staff not in floaters
        for emp_name, months_data in employee_tracking.items():
            if not months_data:
                continue
                
            # Use actual hierarchy level from team data
            hierarchy_level = hierarchy_mapping.get(emp_name, 3)  # Default to junior if not found
            
            # Rule 2: Level 1 employees should not be floaters
            if hierarchy_level == 1:
                for month_name, data in months_data.items():
                    if data['role'] == 'floater':
                        violations.append(f"Rule 2 violated: {emp_name} (Level {hierarchy_level} - {data['designation']}) assigned as floater in {month_name}")
        
        # Rule 3: Check consecutive floater assignments
        for emp_name, months_data in employee_tracking.items():
            floater_months = []
            for month_name in month_names:
                if month_name in months_data and months_data[month_name]['role'] == 'floater':
                    floater_months.append(month_name)
                    
            # Check for consecutive months
            if len(floater_months) > 1:
                # Simple check: if more than one month as floater, check if any are consecutive
                month_indices = [month_names.index(month) for month in floater_months if month in month_names]
                month_indices.sort()
                
                for i in range(len(month_indices) - 1):
                    if month_indices[i+1] - month_indices[i] == 1:
                        violations.append(f"Rule 3 violated: {emp_name} was floater in consecutive months: {month_names[month_indices[i]]} and {month_names[month_indices[i+1]]}")
                        break
        
        # Rule 4: Junior employees must rotate shifts
        for emp_name, months_data in employee_tracking.items():
            if not months_data:
                continue
                
            # Use actual hierarchy level from team data
            hierarchy_level = hierarchy_mapping.get(emp_name, 3)  # Default to junior if not found
            
            # Only check rotation for junior employees (level 3+)
            if hierarchy_level >= 3:
                assigned_shifts = []
                for month_name in month_names:
                    if (month_name in months_data and 
                        months_data[month_name]['role'] == 'assigned_staff'):
                        assigned_shifts.append((month_name, months_data[month_name]['shift']))
                
                # Check for consecutive months with same shift
                for i in range(len(assigned_shifts) - 1):
                    current_month, current_shift = assigned_shifts[i]
                    next_month, next_shift = assigned_shifts[i+1]
                    
                    current_idx = month_names.index(current_month)
                    next_idx = month_names.index(next_month)
                    
                    if (next_idx - current_idx == 1 and current_shift == next_shift):
                        violations.append(f"Rule 4 violated: {emp_name} (Level {hierarchy_level} - {months_data[current_month]['designation']}) worked {current_shift} shift in consecutive months: {current_month} and {next_month}")
        
        return {
            "is_valid": len(violations) == 0,
            "violations": violations,
            "validation_notes": f"Programmatic validation checked {len(employee_tracking)} employees across {len(month_names)} months using actual team hierarchy"
        }
        
    except Exception as e:
        return {
            "is_valid": False,
            "violations": [f"Programmatic validation error: {str(e)}"],
            "validation_notes": "Error during validation"
        }
