from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlmodel import SQLModel, Field, Session, create_engine, select

# --- Security Configuration ---
SECRET_KEY = "YOUR_SUPER_SECRET_KEY_CHANGE_THIS"  # Keep this secret!
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# --- Database Setup ---
DATABASE_URL = "sqlite:///database.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


# --- Database Models ---
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    hashed_password: str
    role: str  # "ADMIN" or "CR"


class Subject(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    code: str
    semester: int


class Student(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    roll_number: str = Field(unique=True, index=True)
    name: str
    semester: int


class Attendance(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    student_id: int
    subject_id: int
    date: str
    status: str  # "PRESENT", "ABSENT", "LEAVE"


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


app = FastAPI(title="Secure CR Attendance System")


@app.on_event("startup")
def on_startup():
    create_db_and_tables()


# --- Pydantic Schemas for Requests ---
class UserCreate(BaseModel):
    username: str
    password: str
    role: str


class SubjectCreate(BaseModel):
    name: str
    code: str
    semester: int


class StudentCreate(BaseModel):
    roll_number: str
    name: str
    semester: int


class AttendanceMark(BaseModel):
    student_id: int
    subject_id: int
    date: str
    status: str


class Token(BaseModel):
    access_token: str
    token_type: str


# --- Helper Functions ---
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


def get_session():
    with Session(engine) as session:
        yield session


def get_current_user(
    token: str = Depends(oauth2_scheme), session: Session = Depends(get_session)
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = session.exec(select(User).where(User.username == username)).first()
    if user is None:
        raise credentials_exception
    return user


def get_current_admin(current_user: User = Depends(get_current_user)):
    if current_user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Not authorized as Admin")
    return current_user


# --- Authentication Route ---
@app.post("/token", response_model=Token)
def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
):
    user = session.exec(
        select(User).where(User.username == form_data.username)
    ).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = jwt.encode(
        {"sub": user.username, "role": user.role},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    return {"access_token": access_token, "token_type": "bearer"}


# --- Admin Routes ---
@app.post("/admin/users")
def create_user(
    user_data: UserCreate,
    session: Session = Depends(get_session),
    admin: User = Depends(get_current_admin),
):
    existing = session.exec(
        select(User).where(User.username == user_data.username)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already registered")

    hashed_pw = get_password_hash(user_data.password)
    new_user = User(
        username=user_data.username, hashed_password=hashed_pw, role=user_data.role
    )
    session.add(new_user)
    session.commit()
    return {"message": "User created successfully"}


@app.post("/admin/subjects")
def create_subject(
    subject: SubjectCreate,
    session: Session = Depends(get_session),
    admin: User = Depends(get_current_admin),
):
    db_subject = Subject.from_orm(subject)
    session.add(db_subject)
    session.commit()
    session.refresh(db_subject)
    return db_subject


# --- CR / Attendance Routes ---
@app.post("/cr/students")
def add_student(
    student: StudentCreate,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    db_student = Student.from_orm(student)
    session.add(db_student)
    session.commit()
    session.refresh(db_student)
    return db_student


@app.post("/cr/attendance/mark")
def mark_attendance(
    attendance: AttendanceMark,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    db_attendance = Attendance.from_orm(attendance)
    session.add(db_attendance)
    session.commit()
    session.refresh(db_attendance)
    return {"message": "Attendance marked successfully"}