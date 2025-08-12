# AI powered Rota Generator 

# 🔄 Shift Scheduling System

A comprehensive Flask-based web application for managing employee shift schedules with intelligent automation and AI-powered validation. This system helps organizations efficiently assign employees to different shifts while ensuring compliance with business rules and fair workload distribution.

## ✨ Features

### 👥 Employee Management
- **User Authentication**: Secure signup/login system with password hashing
- **Designation Management**: Define job roles with hierarchy levels and leave allowances
- **Employee Profiles**: Manage employee information, designations, and leave dates
- **Team Organization**: Create and manage teams with specific shift templates

### 📅 Smart Scheduling
- **Automated Schedule Generation**: Generate monthly shift assignments automatically
- **Multiple Shift Templates**: Support for 3-shift, 4-shift, and 5-shift patterns
- **Hierarchy-Based Rotation**: Different rotation rules based on employee seniority
- **Fair Distribution**: Ensure equitable assignment of shifts and floater duties

### 🤖 AI-Powered Validation
- **Rule Compliance**: Validate schedules against complex business rules
- **Automatic Corrections**: AI-powered schedule fixing for rule violations
- **Real-time Analysis**: Instant feedback on schedule validity
- **Detailed Reporting**: Comprehensive violation reports with explanations

### 📊 Data Export
- **CSV Export**: Download schedules in multiple CSV formats
- **Detailed Reports**: Export comprehensive employee assignment summaries
- **Monthly Breakdowns**: Generate month-by-month schedule analysis

## 🛠️ Technology Stack

- **Backend**: Flask (Python)
- **Database**: MySQL with SQLAlchemy ORM
- **Authentication**: Flask-Login with Werkzeug password hashing
- **AI Integration**: Google Generative AI (Gemini)
- **Frontend**: HTML/CSS with Jinja2 templates
- **Data Validation**: Marshmallow for form validation

## 📋 Prerequisites

- Python 3.8 or higher
- MySQL database server
- Google AI API key (for AI validation features)

## 🚀 Installation

### 1. Clone the Repository
```bash
git clone https://github.com/yourusername/shift-scheduling-system.git
cd shift-scheduling-system
```

### 2. Set Up Virtual Environment
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Environment Configuration
Create a `.env` file in the project root:
```env
SECRET_KEY=your-super-secret-key-here
DATABASE_URL=mysql+pymysql://username:password@localhost/shift_rota
GEMINI_API_KEY=your-google-ai-api-key
```

### 5. Database Setup
```bash
# Create the database
mysql -u root -p
CREATE DATABASE shift_rota;
exit

# Initialize tables
flask initdb
```

### 6. Run the Application
```bash
python app.py
```

The application will be available at `http://localhost:5000`

## 📖 Usage Guide

### Getting Started

1. **Sign Up**: Create an account at `/signup`
2. **Add Designations**: Define job roles with hierarchy levels at `/designation/add`
3. **Add Employees**: Create employee profiles at `/employee/add`
4. **Create Teams**: Form teams with shift templates at `/team/add`
5. **Generate Schedule**: Create automated schedules at `/generate_schedule`

### Scheduling Rules

The system enforces several intelligent rules:

#### **Rule 1: Shift Stability**
- **Senior Staff (Level 1)**: Can work the same shift for up to 3 consecutive months
- **Mid-level Staff (Level 2)**: Can work the same shift for up to 2 consecutive months
- **Junior Staff (Level 3+)**: Must rotate shifts monthly

#### **Rule 2: Floater Exemption**
- Most senior employees are exempt from floater duties

#### **Rule 3: Fair Floater Rotation**
- No employee can be assigned as a floater in consecutive months

#### **Rule 4: Fixed Staffing**
- Each shift maintains the required number of assigned staff

#### **Rule 5: Hierarchy Diversity**
- Shifts should include employees from different hierarchy levels when possible

### Team Configuration

#### Shift Templates
- **3-Shift**: Morning, Afternoon, Night
- **4-Shift**: Morning, Afternoon, Evening, Night
- **5-Shift**: Early Morning, Morning, Afternoon, Evening, Night

#### Team Requirements
- Minimum 2 employees of each gender
- Sufficient staff for all shifts (template × people per shift)
- Mixed hierarchy levels for optimal coverage

## 🔧 Configuration

### Database Configuration
Modify the `DATABASE_URL` in your `.env` file:
```env
DATABASE_URL=mysql+pymysql://username:password@host:port/database_name
```

### AI Features
To enable AI-powered schedule validation and fixing:
1. Get a Google AI API key from [Google AI Studio](https://makersuite.google.com/app/apikey)
2. Add it to your `.env` file as `GEMINI_API_KEY`

### Shift Templates
Modify shift templates in `scheduler.py`:
```python
team_shifts_map = {
    '3-shift': ['Morning', 'Afternoon', 'Night'], 
    '4-shift': ['Morning', 'Afternoon', 'Evening', 'Night'],
    '5-shift': ['Early Morning', 'Morning', 'Afternoon', 'Evening', 'Night']
}
```

## 🔍 API Endpoints

### Authentication
- `GET/POST /signup` - User registration
- `GET/POST /login` - User authentication
- `GET /logout` - User logout

### Management
- `GET/POST /designation/add` - Add job designations
- `GET/POST /designation/manage` - Manage designations
- `GET/POST /employee/add` - Add employees
- `GET/POST /employees/manage` - Manage employees
- `GET/POST /team/add` - Create teams
- `GET/POST /team/manage` - Manage teams

### Scheduling
- `GET/POST /generate_schedule` - Generate and view schedules
- `POST /fix_schedule/<team_id>` - AI-powered schedule fixing
- `POST /delete_schedule/<team_id>` - Delete existing schedules
- `GET /download_schedule_csv/<team_id>` - Export basic CSV
- `GET /download_schedule_detailed_csv/<team_id>` - Export detailed CSV

## 📁 Project Structure

```
shift-scheduling-system/
├── app.py                 # Main Flask application
├── models.py              # Database models
├── routes.py              # URL routes and views
├── scheduler.py           # Scheduling logic and AI integration
├── templates/             # HTML templates
├── static/                # CSS, JS, images
├── requirements.txt       # Python dependencies
├── .env                   # Environment variables (create this)
└── README.md             # This file
```

## 🧪 Testing

### Manual Testing
1. Create test designations with different hierarchy levels
2. Add test employees with various designations
3. Form test teams with different shift templates
4. Generate schedules and verify rule compliance
5. Test AI validation and fixing features

### Validation Testing
The system includes comprehensive validation:
- Programmatic rule checking
- AI-powered validation (when API key is available)
- Real-time feedback on violations

## 🐛 Troubleshooting

### Common Issues

**Database Connection Error**
- Verify MySQL is running
- Check database credentials in `.env`
- Ensure database exists

**AI Features Not Working**
- Verify `GEMINI_API_KEY` is set correctly
- Check internet connection
- Review API key permissions

**Schedule Generation Fails**
- Ensure teams have sufficient employees
- Verify employee leave dates are valid
- Check team gender balance requirements

**Import Errors**
- Ensure all dependencies are installed: `pip install -r requirements.txt`
- Verify virtual environment is activated

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Development Guidelines
- Follow PEP 8 style guidelines
- Add comments for complex logic
- Test new features thoroughly
- Update documentation as needed


## 🙏 Acknowledgments

- Flask framework and its excellent ecosystem
- Google AI for powerful language model integration
- SQLAlchemy for robust database management
- The open-source community for continuous inspiration


**Built with ❤️ for efficient workforce management**
