from flask import Blueprint, render_template, request, redirect, url_for, flash,jsonify,Response
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from marshmallow import Schema, fields, validate, ValidationError
from sqlalchemy.orm import aliased
import json
import os
from collections import defaultdict
from datetime import datetime
import csv
import io

# Import db object and models
from app import db
from models import User, Designation, Employee, Team, TeamMember, SavedSchedule
from scheduler import generate_monthly_assignments, validate_schedule_with_ai, fix_schedule_with_ai, validate_schedule_programmatically,SCHEDULING_RULES_TEXT

main_bp = Blueprint('main', __name__)

#----------------------------------------------------------------------------#
# User Authentication Routes.
#----------------------------------------------------------------------------#
class UserSchema(Schema):
    username = fields.Str(required=True, validate=validate.Length(min=3, error="Username must be at least 3 characters."))
    email = fields.Email(required=True, error_messages={
        "required": "Email is required.",
        "invalid": "Please enter a valid email ID."
    })
    password = fields.Str(required=True, load_only=True)

user_schema = UserSchema()

@main_bp.route('/')
def home():
    return render_template('home.html')

@main_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        data = {
            'username': request.form['username'],
            'email': request.form['email'],
            'password': request.form['password']
        }

        try:
            validated = user_schema.load(data)
        except ValidationError as err:
            for msg in err.messages.values():
                flash(msg[0], 'danger')
            return redirect(url_for('main.signup'))

        if User.query.filter_by(email=validated['email']).first():
            flash('This email ID is already registered.', 'danger')
            return redirect(url_for('main.signup'))

        if User.query.filter_by(username=validated['username']).first():
            flash('This username is already taken.', 'danger')
            return redirect(url_for('main.signup'))

        hashed_pw = generate_password_hash(validated['password'])
        user = User(username=validated['username'], email=validated['email'], password=hashed_pw)
        db.session.add(user)
        db.session.commit()
        flash('Signup successful! You can now login.', 'success')
        return redirect(url_for('main.login'))

    return render_template('signup.html')

@main_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        identifier = request.form['identifier']
        password = request.form['password']
        user = User.query.filter((User.email == identifier) | (User.username == identifier)).first()

        if not user:
            flash('User not found. Please check your email or username.', 'danger')
            return redirect(url_for('main.login'))

        if not check_password_hash(user.password, password):
            flash('Incorrect password.', 'danger')
            return redirect(url_for('main.login'))

        login_user(user)
        return redirect(url_for('main.dashboard'))

    return render_template('login.html')

@main_bp.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', user=current_user)

@main_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out.', 'info')
    return redirect(url_for('main.login'))

#----------------------------------------------------------------------------#
# Management Routes
#----------------------------------------------------------------------------#

@main_bp.route('/designation/add', methods=['GET', 'POST'])
@login_required
def add_designation():
    if request.method == 'POST':
        title = request.form['title'].strip().title()
        hierarchy = request.form['hierarchy']
        leave = request.form['leave']

        try:
            hierarchy_level = int(hierarchy)
        except ValueError:
            flash('Hierarchy must be a number.', 'danger')
            return redirect(url_for('main.add_designation'))

        if Designation.query.filter_by(title=title).first():
            flash('This designation title already exists.', 'danger')
            return redirect(url_for('main.add_designation'))

        if Designation.query.filter_by(hierarchy_level=hierarchy_level).first():
            flash(f'Hierarchy level {hierarchy_level} is already assigned.', 'danger')
            return redirect(url_for('main.add_designation'))

        designation = Designation(
            title=title,
            hierarchy_level=hierarchy_level,
            monthly_leave_allowance=int(leave)
        )
        db.session.add(designation)
        db.session.commit()
        flash('Designation added successfully.', 'success')
        return redirect(url_for('main.manage_designation'))

    return render_template('designation_add.html')


@main_bp.route('/designation/manage', methods=['GET', 'POST'])
@login_required
def manage_designation():
    designations = Designation.query.order_by(Designation.hierarchy_level).all()

    if request.method == 'POST':
        if 'delete_id' in request.form:
            delete_id = int(request.form['delete_id'])
            designation_to_delete = Designation.query.get(delete_id)
            if designation_to_delete:
                db.session.delete(designation_to_delete)
                db.session.commit()
                flash(f'Designation "{designation_to_delete.title}" deleted.', 'info')
                return redirect(url_for('main.manage_designation'))

        new_titles = []
        new_hierarchies = []
        for desig in designations:
            new_title = request.form.get(f"title_{desig.id}").strip()
            new_hierarchy = int(request.form.get(f"hierarchy_{desig.id}"))
            if new_title in new_titles:
                flash(f'Duplicate designation title "{new_title}" found.', 'danger')
                return redirect(url_for('main.manage_designation'))
            new_titles.append(new_title)
            if new_hierarchy in new_hierarchies:
                flash(f'Duplicate hierarchy level "{new_hierarchy}" found.', 'danger')
                return redirect(url_for('main.manage_designation'))
            new_hierarchies.append(new_hierarchy)

        for desig in designations:
            desig.title = request.form.get(f"title_{desig.id}").strip().title()
            desig.hierarchy_level = int(request.form.get(f"hierarchy_{desig.id}"))
            desig.monthly_leave_allowance = int(request.form.get(f"leave_{desig.id}"))

        db.session.commit()
        flash('Changes saved successfully.', 'success')
        return redirect(url_for('main.manage_designation'))

    return render_template('designation_manage.html', designations=designations)

@main_bp.route('/management')
@login_required
def management_dashboard():
    return render_template('management_dashboard.html')

@main_bp.route('/employee/dashboard')
@login_required
def employee_dashboard():
    return render_template('employee_dashboard.html')

@main_bp.route('/employee/add', methods=['GET', 'POST'])
@login_required
def add_employee():
    designations = Designation.query.all()
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        gender = request.form['gender']
        designation_id = int(request.form['designation_id'])
        leave_dates_raw = request.form.get('leave_dates', '')

        if Employee.query.filter_by(email=email).first():
            flash('An employee with this email already exists.', 'danger')
            return redirect(url_for('main.add_employee'))

        leave_dates_list = [d.strip() for d in leave_dates_raw.split(',') if d.strip()]
        today = datetime.today().date()
        parsed_dates = []
        for d in leave_dates_list:
            try:
                parsed = datetime.strptime(d, "%Y-%m-%d").date()
                if parsed < today:
                    flash(f"Leave date {d} is in the past.", "danger")
                    return redirect(url_for('main.add_employee'))
                parsed_dates.append(parsed)
            except ValueError:
                flash(f"Invalid date format: {d}", "danger")
                return redirect(url_for('main.add_employee'))

        designation = Designation.query.get(designation_id)
        max_allowed = designation.monthly_leave_allowance if designation else 0
        month_count = defaultdict(int)
        for date in parsed_dates:
            key = (date.year, date.month)
            month_count[key] += 1
        for (year, month), count in month_count.items():
            if count > max_allowed:
                flash(f"Too many leaves in {year}-{month:02d}. Max allowed is {max_allowed}.", "danger")
                return redirect(url_for('main.add_employee'))

        leave_dates_json = json.dumps([d.strftime("%Y-%m-%d") for d in parsed_dates])
        employee = Employee(
            name=name,
            email=email,
            gender=gender,
            designation_id=designation_id,
            shift_preference=request.form.get('shift_preference') or None,
            leave_dates=leave_dates_json
        )
        db.session.add(employee)
        db.session.commit()
        flash('Employee added successfully!', 'success')
        return redirect(url_for('main.manage_employees'))
    return render_template('employee_add.html', designations=designations)

@main_bp.route('/employees/manage', methods=['GET', 'POST'])
@login_required
def manage_employees():
    employees = Employee.query.all()
    designations = Designation.query.all()
    if request.method == 'POST':
        emp_id = int(request.form['emp_id'])
        employee = Employee.query.get(emp_id)
        if request.form['action'] == 'delete':
            db.session.delete(employee)
            db.session.commit()
            flash('Employee deleted successfully.', 'info')
            return redirect(url_for('main.manage_employees'))

        employee.name = request.form['name']
        employee.email = request.form['email']
        employee.gender = request.form['gender']
        employee.designation_id = int(request.form['designation_id'])
        employee.shift_preference = request.form.get('shift_preference') or None
        raw_dates = request.form.get('leave_dates', '')
        leave_list = [d.strip() for d in raw_dates.split(',') if d.strip()]
        today = datetime.today().date()
        for d in leave_list:
            parsed = datetime.strptime(d, "%Y-%m-%d").date()
            if parsed < today:
                flash('Leave date in the past: {}'.format(d), 'danger')
                return redirect(url_for('main.manage_employees'))
        month_map = {}
        for d in leave_list:
            month_key = d[:7]
            month_map[month_key] = month_map.get(month_key, 0) + 1
        max_allowed = Designation.query.get(employee.designation_id).monthly_leave_allowance
        for month, count in month_map.items():
            if count > max_allowed:
                flash(f'Maximum leaves reached in {month}. Allowed: {max_allowed}', 'danger')
                return redirect(url_for('main.manage_employees'))
        employee.leave_dates = json.dumps(leave_list)
        db.session.commit()
        flash('Changes saved successfully.', 'success')
        return redirect(url_for('main.manage_employees'))
    for emp in employees:
        try:
            emp.leave_dates_formatted = json.loads(emp.leave_dates or '[]')
        except:
            emp.leave_dates_formatted = []
    return render_template('employee_manage.html', employees=employees, designations=designations)

@main_bp.route('/employee/delete', methods=['POST'])
@login_required
def delete_employee():
    emp_id = int(request.form['emp_id'])
    emp = Employee.query.get(emp_id)
    if not emp:
        flash('Employee not found.', 'danger')
        return redirect(url_for('main.manage_employees'))
    db.session.delete(emp)
    db.session.commit()
    flash(f'Employee {emp.name} deleted successfully.', 'info')
    return redirect(url_for('main.manage_employees'))

@main_bp.route('/employee/update', methods=['POST'])
@login_required
def update_employee():
    emp_id = int(request.form['emp_id'])
    designation_id = int(request.form['designation_id'])
    leave_dates = request.form['leave_dates']
    emp = Employee.query.get(emp_id)
    if not emp:
        flash('Employee not found.', 'danger')
        return redirect(url_for('main.manage_employees'))
    emp.designation_id = designation_id
    emp.leave_dates = leave_dates
    db.session.commit()
    flash(f'Changes for {emp.name} saved successfully.', 'success')
    return redirect(url_for('main.manage_employees'))

@main_bp.route('/team/dashboard')
@login_required
def view_teams():
    teams = Team.query.all()
    return render_template('team_dashboard.html', teams=teams)

@main_bp.route('/team/add', methods=['GET', 'POST'])
@login_required
def add_team():
    TeamMemberAlias = aliased(TeamMember)
    unassigned_employees = (
        db.session.query(Employee)
        .outerjoin(TeamMemberAlias, Employee.id == TeamMemberAlias.employee_id)
        .filter(TeamMemberAlias.employee_id == None)
        .all()
    )
    if request.method == 'POST':
        name = request.form['name']
        template = request.form['template']
        people = int(request.form['people'])
        member_ids = list(map(int, request.form.getlist('members')))
        shift_map = {"3-shift": 3, "4-shift": 4, "5-shift": 5}
        shift_count = shift_map.get(template, 0)
        required_min_members = shift_count * people
        if Team.query.filter_by(name=name).first():
            flash('A team with this name already exists.', 'danger')
            return redirect(url_for('main.add_team'))
        if len(member_ids) < required_min_members:
            flash(f"A minimum of {required_min_members} employees required for {template} template with {people} people/shift.", 'danger')
            return redirect(url_for('main.add_team'))
        selected_employees = Employee.query.filter(Employee.id.in_(member_ids)).all()
        male_count = sum(1 for e in selected_employees if e.gender == 'Male')
        female_count = sum(1 for e in selected_employees if e.gender == 'Female')
        if male_count < 2 or female_count < 2:
            flash('A team must include at least 2 members from each gender (minimum 2 males and 2 females).', 'danger')
            return redirect(url_for('main.add_team'))
        team = Team(name=name, shift_template=template, people_per_shift=people)
        db.session.add(team)
        db.session.commit()
        for eid in member_ids:
            db.session.add(TeamMember(team_id=team.id, employee_id=eid))
        db.session.commit()
        flash('Team added successfully.', 'success')
        return redirect(url_for('main.view_teams'))
    return render_template('team_add.html', employees=unassigned_employees)

@main_bp.route('/team/manage', methods=['GET', 'POST'])
@login_required
def manage_teams():
    teams = Team.query.all()
    team_members_map = {team.id: {m.employee_id for m in team.members} for team in teams}
    if request.method == 'POST':
        if request.form['action'] == 'delete':
            team_id = int(request.form['team_id'])
            team = Team.query.get(team_id)
            if team:
                for tm in team.members:
                    db.session.delete(tm)
                db.session.delete(team)
                db.session.commit()
                flash('Team deleted successfully.', 'info')
            return redirect(url_for('main.manage_teams'))
        team_id = int(request.form['team_id'])
        team = Team.query.get(team_id)
        team.name = request.form['name']
        team.shift_template = request.form['template']
        team.people_per_shift = int(request.form['people'])
        selected_ids = set(map(int, request.form.getlist('members')))
        if not selected_ids:
            flash('You must select at least one team member.', 'danger')
            return redirect(url_for('main.manage_teams'))
        shift_multiplier = {'3-shift': 3, '4-shift': 4, '5-shift': 5}.get(team.shift_template, 3)
        required_min = shift_multiplier * team.people_per_shift
        if len(selected_ids) < required_min:
            flash(f'You must select at least {required_min} members for {team.shift_template} with {team.people_per_shift} people per shift.', 'danger')
            return redirect(url_for('main.manage_teams'))
        selected_emps = Employee.query.filter(Employee.id.in_(selected_ids)).all()
        male_count = sum(1 for e in selected_emps if e.gender == 'Male')
        female_count = sum(1 for e in selected_emps if e.gender == 'Female')
        if male_count < 2 and female_count < 2:
            flash('A team must include at least 2 members of the opposite gender.', 'danger')
            return redirect(url_for('main.manage_teams'))
        current_ids = {m.employee_id for m in team.members}
        for emp_id in selected_ids - current_ids:
            db.session.add(TeamMember(team_id=team.id, employee_id=emp_id))
        for tm in team.members[:]:
            if tm.employee_id not in selected_ids:
                db.session.delete(tm)
        db.session.commit()
        flash('Team updated successfully.', 'success')
        return redirect(url_for('main.manage_teams'))
    employee_map = {}
    for team in teams:
        all_assigned_ids = {m.employee_id for t in teams for m in t.members if t.id != team.id}
        available_emps = Employee.query.filter(~Employee.id.in_(all_assigned_ids)).all()
        employee_map[team.id] = available_emps
    return render_template('team_manage.html', teams=teams, employee_map=employee_map, team_members_map=team_members_map)

@main_bp.route('/team/delete/<int:team_id>', methods=['POST'])
@login_required
def delete_team(team_id):
    team = Team.query.get_or_404(team_id)
    db.session.delete(team)
    db.session.commit()
    flash('Team deleted successfully.', 'info')
    return redirect(url_for('main.manage_teams'))

def _build_team_hierarchy_info(team):
    """Helper function to build team hierarchy information for validation."""
    team_hierarchy_info = []
    for member in team.members:
        team_hierarchy_info.append({
            'name': member.employee.name,
            'designation': member.employee.designation.title,
            'hierarchy_level': member.employee.designation.hierarchy_level
        })
    return team_hierarchy_info

# Updated generate_schedule and fix_schedule routes with improved validation

@main_bp.route('/generate_schedule', methods=['GET', 'POST'])
@login_required
def generate_schedule():
    teams = Team.query.all()
    selected_team = None
    schedule_by_month = None
    schedule_exists = False
    ai_validation_report = None
    programmatic_validation_report = None
    
    # --- Handle page load and team selection ---
    if request.method == 'GET':
        team_id = request.args.get('team_id', type=int)
        if team_id:
            selected_team = Team.query.get(team_id)
            if selected_team:
                saved_schedule = SavedSchedule.query.filter_by(team_id=team_id).first()
                if saved_schedule:
                    schedule_by_month = json.loads(saved_schedule.schedule_data)
                    schedule_exists = True
                    
                    # Build team hierarchy information for validation
                    team_hierarchy_info = _build_team_hierarchy_info(selected_team)
                    
                    # Always run programmatic validation (more reliable)
                    programmatic_validation_report = validate_schedule_programmatically(
                        saved_schedule.schedule_data, team_hierarchy_info
                    )
                    
                    # Run AI validation if API key is available
                    api_key = os.getenv('GEMINI_API_KEY')
                    if api_key:
                        try:
                            ai_validation_report = validate_schedule_with_ai(
                                saved_schedule.schedule_data, 
                                SCHEDULING_RULES_TEXT, 
                                api_key,
                                team_hierarchy_info
                            )
                        except Exception as e:
                            ai_validation_report = {
                                "is_valid": False,
                                "violations": [f"AI validation failed: {str(e)}"],
                                "validation_notes": "AI validation error"
                            }
                    
                    # Combine validation reports (prioritize programmatic validation)
                    combined_validation = {
                        "is_valid": programmatic_validation_report.get("is_valid", True),
                        "violations": programmatic_validation_report.get("violations", []),
                        "ai_violations": ai_validation_report.get("violations", []) if ai_validation_report else [],
                        "validation_notes": programmatic_validation_report.get("validation_notes", ""),
                        "ai_notes": ai_validation_report.get("validation_notes", "") if ai_validation_report else "",
                        "team_hierarchy": team_hierarchy_info,  # Include for frontend display
                        "people_per_shift": selected_team.people_per_shift  # Include team config
                    }
                    ai_validation_report = combined_validation

    # --- Handle new schedule generation ---
    if request.method == 'POST':
        team_id = int(request.form['team_id'])
        months = int(request.form.get('months', 1)) 
        selected_team = Team.query.get(team_id)

        if SavedSchedule.query.filter_by(team_id=team_id).first():
            flash("A schedule for this team already exists.", "warning")
            return redirect(url_for('main.generate_schedule', team_id=team_id))

        schedule_by_month = generate_monthly_assignments(selected_team, months)

        if schedule_by_month:
            new_schedule = SavedSchedule(
                team_id=team_id,
                schedule_data=json.dumps(schedule_by_month)
            )
            db.session.add(new_schedule)
            db.session.commit()
            flash("New schedule generated and saved!", "success")
            return redirect(url_for('main.generate_schedule', team_id=team_id))

    return render_template(
        'generate_schedule.html',
        teams=teams,
        selected_team=selected_team,
        schedule_by_month=schedule_by_month,
        schedule_exists=schedule_exists,
        ai_validation_report=ai_validation_report,
        months=1
    )


@main_bp.route('/delete_schedule/<int:team_id>', methods=['POST'])
@login_required
def delete_schedule(team_id):
    """
    Finds and deletes a saved schedule for a given team.
    """
    schedule_to_delete = SavedSchedule.query.filter_by(team_id=team_id).first()
    if schedule_to_delete:
        db.session.delete(schedule_to_delete)
        db.session.commit()
        flash('The existing schedule has been deleted. You can now generate a new one.', 'success')
    else:
        flash('No schedule was found for this team to delete.', 'warning')
    
    return redirect(url_for('main.generate_schedule', team_id=team_id))

# Replace the fix_schedule route in routes.py with this updated version

@main_bp.route('/fix_schedule/<int:team_id>', methods=['POST'])
@login_required
def fix_schedule(team_id):
    """
    Updated AJAX endpoint that only fixes REAL violations using exact validation rules.
    """
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        return jsonify({
            "success": False, 
            "error": "GEMINI_API_KEY not found in environment."
        })

    saved_schedule = SavedSchedule.query.filter_by(team_id=team_id).first()
    if not saved_schedule:
        return jsonify({
            "success": False, 
            "error": "No schedule found to fix."
        })

    team = Team.query.get(team_id)
    if not team:
        return jsonify({
            "success": False, 
            "error": "Team not found."
        })
    
    team_hierarchy_info = _build_team_hierarchy_info(team)

    # Get ONLY the core violations using the exact same validation logic
    validation_result = validate_schedule_programmatically(
        saved_schedule.schedule_data, team_hierarchy_info
    )
    
    # Filter to only core rule violations (ignore Rules 4, 5 which are less critical)
    core_violations = []
    for violation in validation_result.get('violations', []):
        if ("Rule 1 violated:" in violation or 
            "Rule 2 violated:" in violation or 
            "Rule 3 violated:" in violation):
            core_violations.append(violation)
    
    print(f"DEBUG: Found {len(core_violations)} core violations to fix")
    for v in core_violations:
        print(f"DEBUG: Violation: {v}")
    
    if not core_violations:
        return jsonify({
            "success": True, 
            "message": "Schedule is already optimal - no core violations found.",
            "schedule": json.loads(saved_schedule.schedule_data),
            "validation_report": {
                "is_valid": True,
                "violations": [],
                "validation_notes": "No core violations found"
            },
            "no_changes_possible": True
        })

    # Attempt to fix using AI with ONLY the core violations
    ai_result, success = fix_schedule_with_ai(
        saved_schedule.schedule_data,
        core_violations,  # Only pass core violations
        SCHEDULING_RULES_TEXT,
        api_key,
        team_hierarchy_info
    )

    if success:
        corrected_schedule = ai_result.get('schedule')
        changes_made = ai_result.get('changes_made', [])
        violations_fixed = ai_result.get('violations_fixed', [])
        violations_remaining = ai_result.get('violations_remaining', [])
        
        # Validate the corrected schedule structure
        if not corrected_schedule or not isinstance(corrected_schedule, dict):
            return jsonify({
                "success": False,
                "error": "AI returned invalid schedule format"
            })
        
        # Only update the database if AI actually made changes
        if changes_made:
            # Update the database with corrected schedule
            saved_schedule.schedule_data = json.dumps(corrected_schedule)
            saved_schedule.generated_on = datetime.utcnow()
            db.session.commit()
            print("DEBUG: Database updated with corrected schedule")
        
        # Re-validate to confirm the fixes
        final_validation = validate_schedule_programmatically(
            json.dumps(corrected_schedule), team_hierarchy_info
        )
        
        # Filter final violations to core rules only
        final_core_violations = []
        for violation in final_validation.get('violations', []):
            if ("Rule 1 violated:" in violation or 
                "Rule 2 violated:" in violation or 
                "Rule 3 violated:" in violation):
                final_core_violations.append(violation)
        
        combined_validation_report = {
            "is_valid": len(final_core_violations) == 0,
            "violations": final_core_violations,
            "validation_notes": "Re-validated after AI fixes"
        }
        
        # Build success message
        if len(violations_fixed) > 0:
            if len(final_core_violations) == 0:
                message = f"✅ AI successfully fixed all {len(violations_fixed)} violation(s)! Schedule is now optimal."
            else:
                message = f"🔧 AI fixed {len(violations_fixed)} violation(s), but {len(final_core_violations)} issue(s) require manual intervention."
        else:
            message = ai_result.get('message', 'AI analyzed the schedule but could not make improvements.')
        
        return jsonify({
            "success": True,
            "message": message,
            "schedule": corrected_schedule,
            "validation_report": combined_validation_report,
            "changes_made": changes_made,
            "violations_fixed": violations_fixed,
            "remaining_violations": final_core_violations,
            "original_violations_count": len(core_violations),
            "final_violations_count": len(final_core_violations),
            "ai_analysis": ai_result.get('ai_analysis', ''),
            "ai_explanation": ai_result.get('ai_explanation', '')
        })
        
    else:
        error_details = ai_result.get('error', 'Unknown error occurred during AI processing')
        return jsonify({
            "success": False,
            "error": f"AI correction failed: {error_details}"
        })
    
@main_bp.route('/download_schedule_csv/<int:team_id>')
@login_required
def download_schedule_csv(team_id):
    """
    Downloads the saved schedule for a team as a CSV file.
    """
    # Get the team and schedule
    team = Team.query.get_or_404(team_id)
    saved_schedule = SavedSchedule.query.filter_by(team_id=team_id).first()
    
    if not saved_schedule:
        flash('No schedule found for this team.', 'danger')
        return redirect(url_for('main.generate_schedule', team_id=team_id))
    
    try:
        schedule_data = json.loads(saved_schedule.schedule_data)
    except:
        flash('Invalid schedule data format.', 'danger')
        return redirect(url_for('main.generate_schedule', team_id=team_id))
    
    # Create CSV content
    output = io.StringIO()
    writer = csv.writer(output)
    
    # CSV Headers
    writer.writerow([
        'Team Name', 'Month', 'Shift', 'Employee Name', 
        'Employee Designation', 'Role', 'Generated On'
    ])
    
    # Define shift order for consistent output
    shift_order = ['Early Morning', 'Morning', 'Afternoon', 'Evening', 'Night']
    
    # Process each month and shift
    for month_name, shifts in schedule_data.items():
        for shift_name in shift_order:
            if shift_name in shifts:
                shift_data = shifts[shift_name]
                
                # Add assigned staff
                for employee in shift_data.get('assigned_staff', []):
                    writer.writerow([
                        team.name,
                        month_name,
                        shift_name,
                        employee['name'],
                        employee['designation'],
                        'Assigned Staff',
                        saved_schedule.generated_on.strftime('%Y-%m-%d %H:%M') if saved_schedule.generated_on else 'Unknown'
                    ])
                
                # Add floaters
                for employee in shift_data.get('floaters', []):
                    writer.writerow([
                        team.name,
                        month_name,
                        shift_name,
                        employee['name'],
                        employee['designation'],
                        'Floater',
                        saved_schedule.generated_on.strftime('%Y-%m-%d %H:%M') if saved_schedule.generated_on else 'Unknown'
                    ])
    
    # Prepare the response
    csv_content = output.getvalue()
    output.close()
    
    # Generate filename with team name and current date
    filename = f"rota_{team.name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.csv"
    
    # Create response
    response = Response(
        csv_content,
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename={filename}',
            'Content-Type': 'text/csv; charset=utf-8'
        }
    )
    
    return response

@main_bp.route('/download_schedule_detailed_csv/<int:team_id>')
@login_required
def download_schedule_detailed_csv(team_id):
    """
    Downloads a more detailed CSV with monthly summary and employee statistics.
    """
    # Get the team and schedule
    team = Team.query.get_or_404(team_id)
    saved_schedule = SavedSchedule.query.filter_by(team_id=team_id).first()
    
    if not saved_schedule:
        flash('No schedule found for this team.', 'danger')
        return redirect(url_for('main.generate_schedule', team_id=team_id))
    
    try:
        schedule_data = json.loads(saved_schedule.schedule_data)
    except:
        flash('Invalid schedule data format.', 'danger')
        return redirect(url_for('main.generate_schedule', team_id=team_id))
    
    # Create CSV content
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Add header information
    writer.writerow(['TEAM ROTA SCHEDULE - DETAILED EXPORT'])
    writer.writerow([f'Team: {team.name}'])
    writer.writerow([f'Shift Template: {team.shift_template}'])
    writer.writerow([f'People per Shift: {team.people_per_shift}'])
    writer.writerow([f'Generated: {saved_schedule.generated_on.strftime("%Y-%m-%d %H:%M") if saved_schedule.generated_on else "Unknown"}'])
    writer.writerow([f'Exported: {datetime.now().strftime("%Y-%m-%d %H:%M")}'])
    writer.writerow([])  # Empty row
    
    # Monthly breakdown section
    writer.writerow(['MONTHLY SHIFT ASSIGNMENTS'])
    writer.writerow([
        'Month', 'Shift', 'Assigned Staff Names', 'Floater Names', 
        'Total Staff', 'Staff Count', 'Floater Count'
    ])
    
    shift_order = ['Early Morning', 'Morning', 'Afternoon', 'Evening', 'Night']
    
    for month_name, shifts in schedule_data.items():
        for shift_name in shift_order:
            if shift_name in shifts:
                shift_data = shifts[shift_name]
                
                assigned_names = [emp['name'] for emp in shift_data.get('assigned_staff', [])]
                floater_names = [emp['name'] for emp in shift_data.get('floaters', [])]
                
                writer.writerow([
                    month_name,
                    shift_name,
                    '; '.join(assigned_names),
                    '; '.join(floater_names),
                    len(assigned_names) + len(floater_names),
                    len(assigned_names),
                    len(floater_names)
                ])
    
    writer.writerow([])  # Empty row
    
    # Employee summary section
    writer.writerow(['EMPLOYEE ASSIGNMENT SUMMARY'])
    writer.writerow([
        'Employee Name', 'Designation', 'Total Assignments', 
        'Assigned Staff Count', 'Floater Count', 'Months Active'
    ])
    
    # Calculate employee statistics
    employee_stats = defaultdict(lambda: {
        'designation': '',
        'assigned_count': 0,
        'floater_count': 0,
        'months': set()
    })
    
    for month_name, shifts in schedule_data.items():
        for shift_name, shift_data in shifts.items():
            # Count assigned staff
            for employee in shift_data.get('assigned_staff', []):
                emp_name = employee['name']
                employee_stats[emp_name]['designation'] = employee['designation']
                employee_stats[emp_name]['assigned_count'] += 1
                employee_stats[emp_name]['months'].add(month_name)
            
            # Count floaters
            for employee in shift_data.get('floaters', []):
                emp_name = employee['name']
                employee_stats[emp_name]['designation'] = employee['designation']
                employee_stats[emp_name]['floater_count'] += 1
                employee_stats[emp_name]['months'].add(month_name)
    
    # Write employee statistics
    for emp_name, stats in sorted(employee_stats.items()):
        writer.writerow([
            emp_name,
            stats['designation'],
            stats['assigned_count'] + stats['floater_count'],
            stats['assigned_count'],
            stats['floater_count'],
            len(stats['months'])
        ])
    
    # Prepare the response
    csv_content = output.getvalue()
    output.close()
    
    # Generate filename
    filename = f"rota_detailed_{team.name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.csv"
    
    # Create response
    response = Response(
        csv_content,
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename={filename}',
            'Content-Type': 'text/csv; charset=utf-8'
        }
    )
    
    return response
