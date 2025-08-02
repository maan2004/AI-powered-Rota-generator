from flask_login import UserMixin
from app import db
from datetime import datetime

#----------------------------------------------------------------------------#
# Models.
#----------------------------------------------------------------------------#

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)

class Designation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False, unique=True)
    hierarchy_level = db.Column(db.Integer, nullable=False)
    monthly_leave_allowance = db.Column(db.Integer, default=0)

class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    designation_id = db.Column(db.Integer, db.ForeignKey('designation.id'))
    designation = db.relationship('Designation', backref='employees')
    leave_dates = db.Column(db.String(255))
    gender = db.Column(db.String(10))
    shift_preference = db.Column(db.String(50))

class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    shift_template = db.Column(db.String(50))
    people_per_shift = db.Column(db.Integer)

class TeamMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'))
    team = db.relationship('Team', backref='members')
    employee = db.relationship('Employee', backref='teams')

class SavedSchedule(db.Model):
    """Stores a complete, generated monthly schedule for a team."""
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False, unique=True) # Each team can only have one saved schedule
    # We will store the entire schedule as a JSON string.
    # The 'Text' type is used for potentially very long strings.
    schedule_data = db.Column(db.Text, nullable=False) 
    generated_on = db.Column(db.DateTime, default=datetime.utcnow)
    
    team = db.relationship('Team', backref=db.backref('saved_schedule', uselist=False))
