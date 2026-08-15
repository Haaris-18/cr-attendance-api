import os
import io
from datetime import date
from enum import Enum
from typing import List, Optional

import pandas as pd
import matplotlib
matplotlib.use("Agg")  # Non-interactive backend for server environments
import matplotlib.pyplot as plt

from fastapi import FastAPI, HTTPException, Depends, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from sqlalchemy import create_engine, Column, Integer, String, Date, ForeignKey, Enum as SQLEnum, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship

# ------------------------------------------------------------------------------
# 1. DATABASE CONFIGURATION (Neon PostgreSQL)
# ------------------------------------------------------------------------------
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set in environment variables or .env file.")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,  # Maintains active pool connections with Neon
    pool_size=10,
    max_overflow=20
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ------------------------------------------------------------------------------
# 2. SQLALCHEMY MODELS
# ------------------------------------------------------------------------------
class Role(str, Enum):
    ADMIN = "ADMIN"
    CR = "CR"

class AttendanceStatus(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LEAVE = "LEAVE"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password = Column(String, nullable=False)  # In production, store bcrypt hashes
    role = Column(SQLEnum(Role), default=Role.CR, nullable=False)

class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    enrollment_no = Column(String, unique=True, nullable=False, index=True)
    semester = Column(Integer, nullable=False)

    attendances = relationship("Attendance", back_populates="student", cascade="all, delete-orphan")

class Subject(Base):
    __tablename__ = "subjects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    code = Column(String, unique=True, nullable=False)
    semester = Column(Integer, nullable=False)

    attendances = relationship("Attendance", back_populates="subject", cascade="all, delete-orphan")

class Attendance(Base):
    __tablename__ = "attendance"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    date = Column(Date, default=date.today, nullable=False)
    status = Column(SQLEnum(AttendanceStatus), nullable=False)

    student = relationship("Student", back_populates="attendances")
    subject = relationship("Subject", back_populates="attendances")

    __table_args__ = (
        UniqueConstraint("student_id", "subject_id", "date", name="unique_daily_attendance"),
    )

# Create tables in PostgreSQL automatically on startup
Base.metadata.create_all(bind=engine)


# ------------------------------------------------------------------------------
# 3. PYDANTIC SCHEMAS
# ------------------------------------------------------------------------------
class UserCreate(BaseModel):
    username: str
    password: str
    role: Role = Role.CR

class UserResponse(BaseModel):
    id: int
    username: str
    role: Role

    class Config:
        from_attributes = True

class StudentCreate(BaseModel):
    full_name: str
    enrollment_no: str
    semester: int

class StudentResponse(BaseModel):
    id: int
    full_name: str
    enrollment_no: str
    semester: int

    class Config:
        from_attributes = True

class SubjectCreate(BaseModel):
    name: str
    code: str
    semester: int

class SubjectResponse(BaseModel):
    id: int
    name: str
    code: str
    semester: int

    class Config:
        from_attributes = True

class AttendanceRecord(BaseModel):
    student_id: int
    subject_id: int
    date: date
    status: AttendanceStatus


# ------------------------------------------------------------------------------
# 4. FASTAPI APP & DEPENDENCIES
# ------------------------------------------------------------------------------
app = FastAPI(
    title="CR Attendance Management System",
    description="Backend API supporting Admin setup, CR management, attendance corrections, and graph analytics.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ------------------------------------------------------------------------------
# 5. ADMIN ENDPOINTS
# ------------------------------------------------------------------------------
@app.post("/admin/users", response_model=UserResponse)
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.username == user.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    new_user = User(username=user.username, password=user.password, role=user.role)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@app.post("/admin/subjects", response_model=SubjectResponse)
def create_subject(subject: SubjectCreate, db: Session = Depends(get_db)):
    existing = db.query(Subject).filter(Subject.code == subject.code).first()
    if existing:
        raise HTTPException(status_code=400, detail="Subject code already exists")

    new_subject = Subject(**subject.dict())
    db.add(new_subject)
    db.commit()
    db.refresh(new_subject)
    return new_subject


# ------------------------------------------------------------------------------
# 6. CLASS REPRESENTATIVE (CR) ENDPOINTS
# ------------------------------------------------------------------------------
@app.post("/cr/students", response_model=StudentResponse)
def add_student(student: StudentCreate, db: Session = Depends(get_db)):
    existing = db.query(Student).filter(Student.enrollment_no == student.enrollment_no).first()
    if existing:
        raise HTTPException(status_code=400, detail="Student with this enrollment number already exists")

    new_student = Student(**student.dict())
    db.add(new_student)
    db.commit()
    db.refresh(new_student)
    return new_student

@app.get("/cr/students/semester/{semester}", response_model=List[StudentResponse])
def get_students_by_semester(semester: int, db: Session = Depends(get_db)):
    return db.query(Student).filter(Student.semester == semester).all()

@app.post("/cr/attendance/mark")
def mark_or_edit_attendance(record: AttendanceRecord, db: Session = Depends(get_db)):
    # Validate existence of student and subject
    student = db.query(Student).filter(Student.id == record.student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    subject = db.query(Subject).filter(Subject.id == record.subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")

    # Upsert logic: Update existing record or insert standard entry
    existing_record = db.query(Attendance).filter(
        Attendance.student_id == record.student_id,
        Attendance.subject_id == record.subject_id,
        Attendance.date == record.date
    ).first()

    if existing_record:
        existing_record.status = record.status
        db.commit()
        return {"message": "Attendance record updated successfully"}

    new_record = Attendance(
        student_id=record.student_id,
        subject_id=record.subject_id,
        date=record.date,
        status=record.status
    )
    db.add(new_record)
    db.commit()
    return {"message": "Attendance marked successfully"}


# ------------------------------------------------------------------------------
# 7. ANALYTICS & GRAPH ENGINE
# ------------------------------------------------------------------------------
@app.get("/cr/analytics/student/{student_id}/subject/{subject_id}")
def get_student_subject_attendance(student_id: int, subject_id: int, db: Session = Depends(get_db)):
    records = db.query(Attendance).filter(
        Attendance.student_id == student_id,
        Attendance.subject_id == subject_id
    ).all()

    if not records:
        return {
            "student_id": student_id,
            "subject_id": subject_id,
            "total_conducted": 0,
            "present": 0,
            "absent": 0,
            "on_leave": 0,
            "attendance_percentage": 0.0
        }

    total_conducted = len(records)
    present_count = sum(1 for r in records if r.status == AttendanceStatus.PRESENT)
    leave_count = sum(1 for r in records if r.status == AttendanceStatus.LEAVE)
    absent_count = sum(1 for r in records if r.status == AttendanceStatus.ABSENT)

    effective_total = total_conducted - leave_count
    percentage = (present_count / effective_total * 100) if effective_total > 0 else 0.0

    return {
        "student_id": student_id,
        "subject_id": subject_id,
        "total_conducted": total_conducted,
        "present": present_count,
        "absent": absent_count,
        "on_leave": leave_count,
        "attendance_percentage": round(percentage, 2)
    }

@app.get("/cr/analytics/graph/subject/{subject_id}")
def get_subject_attendance_graph(subject_id: int, db: Session = Depends(get_db)):
    results = db.query(Attendance, Student).join(
        Student, Attendance.student_id == Student.id
    ).filter(Attendance.subject_id == subject_id).all()

    if not results:
        raise HTTPException(status_code=404, detail="No attendance records found for this subject")

    data = [
        {
            "enrollment": student.enrollment_no,
            "status": attendance.status.value
        }
        for attendance, student in results
    ]

    df = pd.DataFrame(data)
    
    # Aggregate counts grouped by enrollment and status
    stats = df.groupby(["enrollment", "status"]).size().unstack(fill_value=0)

    for col in ["PRESENT", "ABSENT", "LEAVE"]:
        if col not in stats.columns:
            stats[col] = 0

    stats["Effective_Total"] = (stats["PRESENT"] + stats["ABSENT"] + stats["LEAVE"]) - stats["LEAVE"]
    stats["Percentage"] = (stats["PRESENT"] / stats["Effective_Total"].replace(0, 1)) * 100

    # Plot PNG Image using Matplotlib
    plt.figure(figsize=(10, 5))
    bars = plt.bar(stats.index, stats["Percentage"], color="#3498db")
    plt.axhline(75, color="#e74c3c", linestyle="--", label="75% Requirement")
    
    plt.title(f"Subject {subject_id} Student Attendance Percentage", fontsize=14)
    plt.xlabel("Enrollment Number", fontsize=12)
    plt.ylabel("Attendance (%)", fontsize=12)
    plt.ylim(0, 105)
    plt.legend()
    plt.tight_layout()

    # Save to memory stream instead of writing to local disk
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()

    return Response(content=buf.getvalue(), media_type="image/png")


# ------------------------------------------------------------------------------
# 8. EXECUTION BOILERPLATE
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)