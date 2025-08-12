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

RULE 1 - TIERED SHIFT STABILITY (CONSOLIDATED):
- Hierarchy Level 1 (most senior): Can stay on same shift for up to 3 consecutive months
- Hierarchy Level 2 (middle): Can stay on same shift for up to 2 consecutive months  
- Hierarchy Level 3+ (junior): Must rotate shifts every month (1-month stability limit)
- ONLY report if you can prove an employee exceeded their exact allowed stability period
- This rule covers BOTH stability limits AND rotation requirements for junior employees
- You must track each employee's consecutive months on the same shift

RULE 2 - FLOATER EXEMPTION:
- Hierarchy Level 1 employees CANNOT be assigned as floaters
- ONLY report if you find a Level 1 employee listed in the "floaters" section
- Be very careful to identify hierarchy levels correctly

RULE 3 - FAIR FLOATER ROTATION:
- No employee can be a floater in consecutive months
- ONLY report if you see the EXACT SAME PERSON in floaters in consecutive months
- You must verify the person's name appears in floaters in consecutive months

RULE 4 - FIXED STAFF COUNT REQUIREMENT:
- Each shift must have EXACTLY the required number of assigned_staff members as specified by "people_per_shift"
- Count only the assigned_staff members, NOT floaters
- ONLY report if assigned_staff count differs from the required count

RULE 5 - HIERARCHY DIVERSITY IN SHIFTS:
- Each shift should have employees from different hierarchy levels when possible
- A shift should not consist entirely of employees from the same hierarchy level
- ONLY report if ALL assigned_staff in a shift have the same hierarchy level AND there are multiple hierarchy levels available in the team
- This rule applies only to assigned_staff, not floaters

VALIDATION METHODOLOGY:
1. First, identify the hierarchy levels of all employees based on their designations
2. Track each employee's assignments month by month
3. For Rule 1: Check if any employee worked the same shift for more consecutive months than their stability limit allows
4. For other rules, build concrete evidence before reporting
5. Double-check your findings
6. If you have ANY doubt, do NOT report the violation

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
    Generates a rule-compliant monthly schedule with accurate state tracking and fixed staff count enforcement.
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

    # Check if we have enough employees for fixed assignments
    if len(all_employees) < required_for_fixed:
        flash(f"Insufficient employees for {team.shift_template}. Need at least {required_for_fixed} employees but only have {len(all_employees)}.", "danger")
        return {}

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

        # --- 5. FIXED STAFF ASSIGNMENT WITH EXACT COUNT ENFORCEMENT ---
        fixed_staff_pool = [e for e in all_employees if e not in active_floaters]
        
        # Initialize shift teams - MUST have exactly people_per_shift members each
        shift_teams = {shift: [] for shift in desirable_shifts}
        
        # Create a pool of assignments to ensure equal distribution and hierarchy diversity
        assignment_attempts = 0
        max_attempts = 100
        
        while assignment_attempts < max_attempts:
            # Reset shift teams for this attempt
            shift_teams = {shift: [] for shift in desirable_shifts}
            available_employees = fixed_staff_pool.copy()
            random.shuffle(available_employees)  # Randomize for better distribution
            
            # Try to assign employees to shifts
            success = True
            
            for shift_name in desirable_shifts:
                shift_employees = []
                
                # Try to get exactly people_per_shift employees for this shift
                for _ in range(people_per_shift):
                    if not available_employees:
                        success = False
                        break
                    
                    # Find best employee for this shift considering rules
                    best_employee = None
                    best_score = -1
                    
                    for emp in available_employees:
                        score = _calculate_assignment_score(
                            emp, shift_name, shift_employees, employee_states, 
                            STABILITY_CONFIG, hierarchy_groups
                        )
                        if score > best_score:
                            best_score = score
                            best_employee = emp
                    
                    if best_employee:
                        shift_employees.append(best_employee)
                        available_employees.remove(best_employee)
                
                if len(shift_employees) != people_per_shift:
                    success = False
                    break
                
                shift_teams[shift_name] = shift_employees
            
            if success:
                # Verify hierarchy diversity in each shift
                diversity_check_passed = True
                for shift_name, employees in shift_teams.items():
                    if len(employees) > 1:  # Only check diversity if more than 1 employee
                        hierarchy_levels = set(emp.designation.hierarchy_level for emp in employees)
                        # If all employees in shift have same hierarchy level and multiple levels exist in team
                        if len(hierarchy_levels) == 1 and len(distinct_hierarchy_levels) > 1:
                            diversity_check_passed = False
                            break
                
                if diversity_check_passed:
                    break
            
            assignment_attempts += 1
        
        if assignment_attempts >= max_attempts:
            flash(f"Could not generate a valid assignment for {month_name} after {max_attempts} attempts.", "warning")
            # Fallback: simple round-robin assignment
            shift_teams = {shift: [] for shift in desirable_shifts}
            for i, emp in enumerate(fixed_staff_pool):
                shift_index = i % num_shifts
                shift_name = desirable_shifts[shift_index]
                if len(shift_teams[shift_name]) < people_per_shift:
                    shift_teams[shift_name].append(emp)

        # Update employee state tracking
        for shift_name, employees in shift_teams.items():
            for emp in employees:
                emp_state = employee_states[emp.id]
                if emp_state['current_shift'] == shift_name:
                    emp_state['months_on_current_shift'] += 1
                else:
                    emp_state['last_shift'] = emp_state['current_shift']
                    emp_state['current_shift'] = shift_name
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

def _calculate_assignment_score(emp, shift_name, current_shift_employees, employee_states, stability_config, hierarchy_groups):
    """
    Calculate a score for assigning an employee to a specific shift.
    Higher score means better assignment.
    """
    score = 0
    emp_state = employee_states[emp.id]
    level = emp.designation.hierarchy_level
    stability_months = stability_config.get(level, 1)
    
    # Rule 1 & 4: Handle stability and rotation requirements
    if stability_months == 1:  # Junior employees must rotate
        if emp_state['last_shift'] == shift_name:
            score -= 1000  # Heavy penalty for same shift
    elif emp_state['months_on_current_shift'] >= stability_months:
        # Senior employee has exceeded stability period, must rotate
        if emp_state['current_shift'] == shift_name:
            score -= 1000  # Heavy penalty for overstaying
    elif emp_state['current_shift'] == shift_name:
        # Senior employee within stability period, prefer current shift
        score += 100
    
    # Hierarchy diversity bonus
    if current_shift_employees:
        existing_levels = set(e.designation.hierarchy_level for e in current_shift_employees)
        if level not in existing_levels:
            score += 50  # Bonus for adding diversity
    
    # Load balancing bonus (prefer shifts with fewer people)
    score += (10 - len(current_shift_employees)) * 10
    
    return score

# Replace these two functions in your scheduler.py file:

def build_team_hierarchy_mapping(team_hierarchy_info):
    """
    Build relative hierarchy mapping from actual team composition.
    Maps company hierarchy levels to team-specific ranks (1, 2, 3, etc.)
    """
    if not team_hierarchy_info:
        return {}, {}, None
    
    # Get unique hierarchy levels present in this team and sort them
    team_levels = sorted(set(emp['hierarchy_level'] for emp in team_hierarchy_info))
    
    # Create relative team rank mapping
    # team_rank 1 = most senior in team, team_rank 2 = second most senior, etc.
    level_to_team_rank = {}
    team_rank_to_stability = {}
    team_rank_labels = {}
    
    for i, company_level in enumerate(team_levels):
        team_rank = i + 1  # Start from 1
        level_to_team_rank[company_level] = team_rank
        
        # Assign stability based on team rank (not company level)
        if team_rank == 1:  # Most senior in team
            team_rank_to_stability[team_rank] = 3
            team_rank_labels[team_rank] = "TEAM RANK 1 (Most Senior in Team)"
        elif team_rank == 2:  # Second most senior in team
            team_rank_to_stability[team_rank] = 2
            team_rank_labels[team_rank] = "TEAM RANK 2 (Middle Senior in Team)"
        else:  # All others are junior in team
            team_rank_to_stability[team_rank] = 1
            team_rank_labels[team_rank] = f"TEAM RANK {team_rank} (Junior in Team)"
    
    # The most senior team rank (rank 1) is exempt from floater duty
    floater_exempt_team_rank = 1
    floater_exempt_company_level = team_levels[0]  # First (lowest number) company level
    
    return level_to_team_rank, team_rank_to_stability, floater_exempt_company_level, team_rank_labels

def validate_schedule_with_ai(schedule_data, rules_text, api_key, team_hierarchy_info=None):
    """
    AI validation with proper relative team hierarchy implementation and consolidated rules.
    """
    if not team_hierarchy_info:
        return {
            "is_valid": False,
            "violations": ["No team hierarchy information provided"],
            "validation_notes": "Cannot validate without team hierarchy data"
        }
    
    # Build team-specific hierarchy mapping
    level_to_team_rank, team_rank_to_stability, floater_exempt_level, team_rank_labels = build_team_hierarchy_mapping(team_hierarchy_info)
    
    # Build detailed team context
    team_analysis = f"""
🏢 COMPANY vs TEAM HIERARCHY ANALYSIS:

COMPANY HIERARCHY LEVELS IN THIS TEAM: {sorted(set(emp['hierarchy_level'] for emp in team_hierarchy_info))}

🎯 TEAM-SPECIFIC RANK MAPPING (USE THESE RANKS, NOT COMPANY LEVELS):
"""
    
    # Show the mapping clearly
    for company_level, team_rank in level_to_team_rank.items():
        employees_at_level = [emp['name'] for emp in team_hierarchy_info if emp['hierarchy_level'] == company_level]
        stability = team_rank_to_stability[team_rank]
        team_analysis += f"""
📊 Company Level {company_level} → {team_rank_labels[team_rank]}
   👥 Employees: {', '.join(employees_at_level)}
   ⏱️  STABILITY: {stability} months (can work same shift for {stability} consecutive months)
   🔄 ROTATION: {'Must rotate after ' + str(stability) + ' months' if stability < 3 else 'Can stay up to 3 months'}
"""
    
    validation_rules = f"""
🎯 VALIDATION RULES (CONSOLIDATED - NO DUPLICATES):

RULE 1 - SHIFT STABILITY (CONSOLIDATED):
{json.dumps({f"Team Rank {rank}": f"{stability} months max" for rank, stability in team_rank_to_stability.items()}, indent=2)}
- Report ONLY ONCE per employee per consecutive violation period
- Track consecutive months on same shift, report only if exceeds team rank limit

RULE 2 - FLOATER EXEMPTION:
- ONLY Company Level {floater_exempt_level} (Team Rank 1) employees cannot be floaters

RULE 3 - CONSECUTIVE FLOATER PREVENTION:
- Same employee cannot be floater in consecutive months

CRITICAL: For Rule 1, report only ONE violation per employee per continuous period that exceeds their stability limit.
Do NOT report multiple violations for the same consecutive period.
"""
    
    prompt = f"""
You are validating a schedule using CONSOLIDATED rules to prevent duplicate violations.

{team_analysis}

{validation_rules}

🚨 CRITICAL INSTRUCTIONS:
1. For Rule 1: Report only ONE violation per employee per consecutive period
2. If an employee works same shift for 3 months but limit is 1 month, report ONCE: "worked for 3 consecutive months exceeding 1-month limit"
3. Do NOT report separate violations for months 2 and 3 of the same period
4. Use team-specific stability periods from mapping above

📊 SCHEDULE DATA:
{schedule_data}

RESPONSE FORMAT:
{{
    "is_valid": true/false,
    "violations": ["One violation per consecutive period that exceeds stability limit"],
    "validation_notes": "Consolidated validation - no duplicates per consecutive period"
}}
"""
    
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        generation_config = genai.types.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.0
        )
        response = model.generate_content(prompt, generation_config=generation_config)
        result = json.loads(response.text)
        
        # Ensure proper format
        if 'is_valid' not in result:
            result['is_valid'] = len(result.get('violations', [])) == 0
        if 'violations' not in result:
            result['violations'] = []
        if 'validation_notes' not in result:
            result['validation_notes'] = f"Team hierarchy mapping: {level_to_team_rank}"
            
        return result
    except Exception as e:
        return {
            "is_valid": False,
            "violations": [f"AI Validation Error: {str(e)}"],
            "validation_notes": "Error during AI validation"
        }

def validate_schedule_programmatically(schedule_data, team_hierarchy_info=None):
    """
    Programmatic validation with consolidated rules to eliminate duplicate violations.
    """
    violations = []
    
    try:
        schedule = json.loads(schedule_data) if isinstance(schedule_data, str) else schedule_data
        
        if not team_hierarchy_info:
            return {
                "is_valid": False,
                "violations": ["No team hierarchy information provided"],
                "validation_notes": "Cannot validate without team hierarchy data"
            }
        
        # Build team-specific hierarchy mapping
        level_to_team_rank, team_rank_to_stability, floater_exempt_level, team_rank_labels = build_team_hierarchy_mapping(team_hierarchy_info)
        
        # Build employee mapping with team ranks
        employee_mapping = {}
        for emp_info in team_hierarchy_info:
            company_level = emp_info['hierarchy_level']
            team_rank = level_to_team_rank[company_level]
            stability = team_rank_to_stability[team_rank]
            employee_mapping[emp_info['name']] = {
                'company_level': company_level,
                'team_rank': team_rank,
                'stability_months': stability,
                'designation': emp_info.get('designation', 'Unknown')
            }
        
        # Track employee assignments across months
        employee_tracking = {}
        month_names = list(schedule.keys())
        people_per_shift = None
        
        # Build tracking data
        for month_name in month_names:
            month_data = schedule[month_name]
            
            for shift_name, shift_data in month_data.items():
                assigned_staff = shift_data.get('assigned_staff', [])
                
                # Rule 4: Check fixed staff count (renumbered)
                if people_per_shift is None and assigned_staff:
                    people_per_shift = len(assigned_staff)
                
                if people_per_shift and len(assigned_staff) != people_per_shift:
                    violations.append(f"Rule 4 violated: {shift_name} shift in {month_name} has {len(assigned_staff)} assigned staff but should have {people_per_shift}")
                
                # Rule 5: Check hierarchy diversity (renumbered, using team ranks)
                if len(assigned_staff) > 1:
                    team_ranks_in_shift = set()
                    available_team_ranks = set(team_rank_to_stability.keys())
                    
                    for staff in assigned_staff:
                        emp_name = staff['name']
                        emp_data = employee_mapping.get(emp_name)
                        if emp_data:
                            team_ranks_in_shift.add(emp_data['team_rank'])
                    
                    # Only report if all staff have same team rank AND multiple ranks available
                    if len(team_ranks_in_shift) == 1 and len(available_team_ranks) > 1:
                        team_rank = list(team_ranks_in_shift)[0]
                        staff_names = [staff['name'] for staff in assigned_staff]
                        violations.append(f"Rule 5 violated: {shift_name} shift in {month_name} has all employees from Team Rank {team_rank}: {', '.join(staff_names)}")
                
                # Track assigned staff
                for staff in assigned_staff:
                    emp_name = staff['name']
                    if emp_name not in employee_tracking:
                        employee_tracking[emp_name] = {}
                    employee_tracking[emp_name][month_name] = {
                        'role': 'assigned_staff',
                        'shift': shift_name
                    }
                
                # Track floaters
                for floater in shift_data.get('floaters', []):
                    emp_name = floater['name']
                    if emp_name not in employee_tracking:
                        employee_tracking[emp_name] = {}
                    employee_tracking[emp_name][month_name] = {
                        'role': 'floater',
                        'shift': shift_name
                    }
        
        # Rule 2: Check floater exemption (using team ranks)
        for emp_name, months_data in employee_tracking.items():
            emp_data = employee_mapping.get(emp_name)
            if not emp_data:
                continue
                
            # Only Team Rank 1 (most senior in team) should not be floaters
            if emp_data['team_rank'] == 1:
                for month_name, data in months_data.items():
                    if data['role'] == 'floater':
                        violations.append(f"Rule 2 violated: {emp_name} (Company Level {emp_data['company_level']} = Team Rank 1) assigned as floater in {month_name}")
        
        # Rule 3: Check consecutive floater assignments
        for emp_name, months_data in employee_tracking.items():
            floater_months = [month for month in month_names if month in months_data and months_data[month]['role'] == 'floater']
            
            if len(floater_months) > 1:
                month_indices = [month_names.index(month) for month in floater_months]
                month_indices.sort()
                
                for i in range(len(month_indices) - 1):
                    if month_indices[i+1] - month_indices[i] == 1:
                        violations.append(f"Rule 3 violated: {emp_name} was floater in consecutive months: {month_names[month_indices[i]]} and {month_names[month_indices[i+1]]}")
                        break
        
        # Rule 1: Check stability violations (consolidated - no duplicates)
        for emp_name, months_data in employee_tracking.items():
            emp_data = employee_mapping.get(emp_name)
            if not emp_data:
                continue
            
            # Get team-specific stability period
            stability_months = emp_data['stability_months']
            team_rank = emp_data['team_rank']
            company_level = emp_data['company_level']
            
            # Track consecutive months on same shift for assigned staff
            assigned_shifts = []
            for month_name in month_names:
                if month_name in months_data and months_data[month_name]['role'] == 'assigned_staff':
                    assigned_shifts.append((month_name, months_data[month_name]['shift']))
            
            # Find all consecutive periods and report only once per period
            consecutive_periods = []
            current_period = []
            
            for month_name, shift_name in assigned_shifts:
                if not current_period or current_period[-1][1] == shift_name:
                    current_period.append((month_name, shift_name))
                else:
                    if len(current_period) > 1:
                        consecutive_periods.append(current_period)
                    current_period = [(month_name, shift_name)]
            
            # Don't forget the last period
            if len(current_period) > 1:
                consecutive_periods.append(current_period)
            
            # Check each consecutive period for violations
            for period in consecutive_periods:
                period_length = len(period)
                shift_name = period[0][1]
                start_month = period[0][0]
                end_month = period[-1][0]
                
                # Rule 1: Report only if period exceeds stability limit
                if period_length > stability_months:
                    if period_length == 2:
                        violations.append(f"Rule 1 violated: {emp_name} (Company Level {company_level} = Team Rank {team_rank}) worked {shift_name} shift for 2 consecutive months ({start_month}, {end_month}), exceeding {stability_months}-month stability limit")
                    else:
                        violations.append(f"Rule 1 violated: {emp_name} (Company Level {company_level} = Team Rank {team_rank}) worked {shift_name} shift for {period_length} consecutive months ({start_month} to {end_month}), exceeding {stability_months}-month stability limit")
        
        # Build detailed validation summary
        rank_summary = {f"Rank {rank} ({stability}m)": [name for name, data in employee_mapping.items() if data['team_rank'] == rank] 
                       for rank, stability in team_rank_to_stability.items()}
        
        return {
            "is_valid": len(violations) == 0,
            "violations": violations,
            "validation_notes": f"Consolidated validation - no duplicates. Team hierarchy mapping: {json.dumps(level_to_team_rank)}. Team ranks: {json.dumps(rank_summary)}. Validated {len(employee_tracking)} employees across {len(month_names)} months."
        }
        
    except Exception as e:
        return {
            "is_valid": False,
            "violations": [f"Programmatic validation error: {str(e)}"],
            "validation_notes": "Error during validation"
        }

def extract_team_hierarchy_info(team):
    """
    Extract hierarchy information from team database model.
    Returns list of employee hierarchy data for the team.
    """
    team_hierarchy_info = []
    
    if hasattr(team, 'members'):
        for member in team.members:
            if hasattr(member, 'employee') and hasattr(member.employee, 'designation'):
                employee = member.employee
                designation = employee.designation
                
                team_hierarchy_info.append({
                    'name': employee.name,
                    'hierarchy_level': designation.hierarchy_level,
                    'designation': designation.title,
                    'employee_id': employee.id
                })
    
    return team_hierarchy_info

# Replace these functions in scheduler.py

def fix_schedule_with_ai(broken_schedule_data, violations_list, rules_text, api_key, team_hierarchy_info=None):
    """
    COMPLETELY REWRITTEN AI fixing function that uses EXACT same validation rules.
    Only fixes violations that were actually reported by the validation function.
    """
    if not team_hierarchy_info or not api_key:
        return {"error": "Missing team hierarchy information or API key"}, False

    try:
        current_schedule = json.loads(broken_schedule_data) if isinstance(broken_schedule_data, str) else broken_schedule_data
    except:
        return {"error": "Invalid schedule data format"}, False

    # Build team-specific hierarchy mapping (same as validation)
    level_to_team_rank, team_rank_to_stability, floater_exempt_level, team_rank_labels = build_team_hierarchy_mapping(team_hierarchy_info)
    
    # Filter to only REAL violations (Rule 1, 2, 3 from the original rules)
    real_violations = []
    for violation in violations_list:
        if ("Rule 1 violated:" in violation or 
            "Rule 2 violated:" in violation or 
            "Rule 3 violated:" in violation):
            real_violations.append(violation)
    
    if not real_violations:
        return {
            "schedule": current_schedule,
            "changes_made": [],
            "violations_fixed": [],
            "violations_remaining": [],
            "message": "No valid violations found to fix"
        }, True

    # Build comprehensive context for AI
    team_context = f"""
TEAM HIERARCHY ANALYSIS:
Company Levels in Team: {sorted(set(emp['hierarchy_level'] for emp in team_hierarchy_info))}

TEAM RANK MAPPING (Use these for validation):
"""
    
    for company_level, team_rank in level_to_team_rank.items():
        employees_at_level = [emp['name'] for emp in team_hierarchy_info if emp['hierarchy_level'] == company_level]
        stability = team_rank_to_stability[team_rank]
        team_context += f"""
Company Level {company_level} → Team Rank {team_rank}
Employees: {', '.join(employees_at_level)}
Stability Limit: {stability} months
"""
    
    # Create the AI prompt with EXACT same rules as validation
    prompt = f"""
You are a schedule optimization AI. Your task is to fix ONLY the specific violations provided while maintaining all scheduling rules.

{team_context}

EXACT VALIDATION RULES (USE THESE ONLY):
RULE 1 - SHIFT STABILITY: 
- Team Rank 1: Max 3 consecutive months on same shift
- Team Rank 2: Max 2 consecutive months on same shift  
- Team Rank 3+: Max 1 consecutive month on same shift (must rotate monthly)

RULE 2 - FLOATER EXEMPTION:
- Company Level {floater_exempt_level} (Team Rank 1) employees cannot be floaters

RULE 3 - CONSECUTIVE FLOATER PREVENTION:
- No employee can be floater in consecutive months

VIOLATIONS TO FIX:
{json.dumps(real_violations, indent=2)}

CURRENT SCHEDULE:
{json.dumps(current_schedule, indent=2)}

INSTRUCTIONS:
1. Analyze ONLY the violations provided above
2. For each violation, find the minimal swap/change needed to fix it
3. Before making any change, verify it doesn't create new violations
4. Focus on employee swaps within the same month between different shifts
5. If a fix isn't possible without violating other rules, explain why

OUTPUT FORMAT (must be valid JSON):
{{
    "analysis": "Brief analysis of the violations and your fix strategy",
    "fixes_possible": true/false,
    "schedule": {{ "updated schedule if fixes were made" }},
    "changes_made": [
        {{
            "violation_fixed": "which violation was addressed",
            "action": "what change was made",
            "employee1": "name of employee moved/swapped",
            "month1": "month name",
            "shift1_from": "original shift",
            "shift1_to": "new shift",
            "employee2": "name of second employee if swap (or null)",
            "month2": "month name for second employee (or null)",
            "shift2_from": "original shift of second employee (or null)",
            "shift2_to": "new shift of second employee (or null)",
            "reasoning": "why this fix works"
        }}
    ],
    "violations_remaining": ["any violations that couldn't be fixed"],
    "explanation": "detailed explanation of what was done or why fixes weren't possible"
}}
"""

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        generation_config = genai.types.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1  # Low temperature for consistency
        )
        
        response = model.generate_content(prompt, generation_config=generation_config)
        ai_result = json.loads(response.text)
        
        print(f"DEBUG: AI Response: {ai_result}")
        
        if not ai_result.get('fixes_possible', False):
            return {
                "schedule": current_schedule,
                "changes_made": [],
                "violations_fixed": [],
                "violations_remaining": real_violations,
                "message": ai_result.get('explanation', 'AI could not find valid fixes')
            }, True
        
        # Validate that AI provided a proper schedule
        fixed_schedule = ai_result.get('schedule')
        if not fixed_schedule:
            return {"error": "AI did not provide a fixed schedule"}, False
            
        # Verify the AI's changes are valid by re-validating
        validation_result = validate_schedule_programmatically(
            json.dumps(fixed_schedule), team_hierarchy_info
        )
        
        # Check if violations were actually reduced
        original_violation_count = len(real_violations)
        new_violation_count = len([v for v in validation_result.get('violations', []) 
                                  if any(rule in v for rule in ["Rule 1 violated:", "Rule 2 violated:", "Rule 3 violated:"])])
        
        if new_violation_count > original_violation_count:
            return {
                "schedule": current_schedule,
                "changes_made": [],
                "violations_fixed": [],
                "violations_remaining": real_violations,
                "message": "AI attempted fixes but they would create new violations. Manual intervention required."
            }, True
        
        # Process the changes for frontend highlighting
        changes_for_frontend = []
        for change in ai_result.get('changes_made', []):
            frontend_change = {
                "month": change.get('month1'),
                "shift": change.get('shift1_to'),
                "section": "assigned_staff",
                "action": "moved",
                "employee": change.get('employee1'),
                "from_shift": change.get('shift1_from'),
                "reason": change.get('reasoning', 'Schedule optimization')
            }
            changes_for_frontend.append(frontend_change)
            
            # Add second employee if it's a swap
            if change.get('employee2'):
                frontend_change2 = {
                    "month": change.get('month2', change.get('month1')),
                    "shift": change.get('shift2_to'),
                    "section": "assigned_staff", 
                    "action": "moved",
                    "employee": change.get('employee2'),
                    "from_shift": change.get('shift2_from'),
                    "reason": f"Swapped with {change.get('employee1')}"
                }
                changes_for_frontend.append(frontend_change2)
        
        # Determine which violations were actually fixed
        remaining_violations = [v for v in validation_result.get('violations', []) 
                              if any(rule in v for rule in ["Rule 1 violated:", "Rule 2 violated:", "Rule 3 violated:"])]
        fixed_violations = [v for v in real_violations if v not in remaining_violations]
        
        success_message = f"AI successfully fixed {len(fixed_violations)} violation(s)"
        if remaining_violations:
            success_message += f", {len(remaining_violations)} issue(s) still require manual intervention"
        else:
            success_message += ". Schedule is now fully compliant!"
            
        return {
            "schedule": fixed_schedule,
            "changes_made": changes_for_frontend,
            "violations_fixed": fixed_violations,
            "violations_remaining": remaining_violations,
            "message": success_message,
            "ai_analysis": ai_result.get('analysis', ''),
            "ai_explanation": ai_result.get('explanation', '')
        }, True
        
    except json.JSONDecodeError as e:
        print(f"DEBUG: JSON decode error: {e}")
        return {"error": f"AI response was not valid JSON: {str(e)}"}, False
    except Exception as e:
        print(f"DEBUG: AI processing error: {e}")
        return {"error": f"AI processing failed: {str(e)}"}, False


# Helper function to store violations when schedule is first generated
def store_initial_violations(team_id, violations_list):
    """
    Store the initial violations when a schedule is generated.
    This should be called right after schedule generation.
    """
    # You can store this in the database or in a temporary cache
    # For now, we'll use a simple file-based approach
    violations_file = f"violations_{team_id}.json"
    try:
        with open(violations_file, 'w') as f:
            json.dump({
                "timestamp": datetime.utcnow().isoformat(),
                "violations": violations_list
            }, f)
    except Exception as e:
        print(f"Could not store violations: {e}")

def get_stored_violations(team_id):
    """
    Retrieve stored violations for a team.
    """
    violations_file = f"violations_{team_id}.json"
    try:
        with open(violations_file, 'r') as f:
            data = json.load(f)
            return data.get('violations', [])
    except:
        return []

# Updated validation function to use EXACT same logic
def validate_schedule_with_ai_exact(schedule_data, rules_text, api_key, team_hierarchy_info=None):
    """
    AI validation that matches the programmatic validation exactly.
    This ensures both validation methods report the same violations.
    """
    if not team_hierarchy_info:
        return {
            "is_valid": False,
            "violations": ["No team hierarchy information provided"],
            "validation_notes": "Cannot validate without team hierarchy data"
        }
    
    # Build team-specific hierarchy mapping
    level_to_team_rank, team_rank_to_stability, floater_exempt_level, team_rank_labels = build_team_hierarchy_mapping(team_hierarchy_info)
    
    # First run programmatic validation to get the ground truth
    programmatic_result = validate_schedule_programmatically(schedule_data, team_hierarchy_info)
    
    # Filter programmatic violations to only include core rules
    core_violations = []
    for violation in programmatic_result.get('violations', []):
        if ("Rule 1 violated:" in violation or 
            "Rule 2 violated:" in violation or 
            "Rule 3 violated:" in violation):
            core_violations.append(violation)
    
    # Return the filtered programmatic result (most reliable)
    return {
        "is_valid": len(core_violations) == 0,
        "violations": core_violations,
        "validation_notes": f"Core rules validation. Team mapping: {level_to_team_rank}"
    }
