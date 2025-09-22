from fastapi import FastAPI, HTTPException, Query, Path, Depends, Header, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from uuid import uuid4, UUID
from datetime import datetime, timedelta
import logging

app = FastAPI(
    title="Super ToDo API por IP",
    description="API avanzada para gestionar tareas con almacenamiento segregado por IP y notificaciones.",
    version="2.1",
)

# Configuración de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En producción, restringir los orígenes
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuración de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Dependencia para autenticar mediante API Key en endpoints de escritura
async def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != "secret-api-key":
        raise HTTPException(status_code=401, detail="API Key inválida")
    return x_api_key

# ---------------------------
# Modelos de datos
# ---------------------------
class Task(BaseModel):
    id: UUID = Field(default_factory=uuid4, description="Identificador único de la tarea")
    title: str = Field(..., example="Comprar leche")
    description: Optional[str] = Field(None, example="Leche semidesnatada")
    completed: bool = Field(default=False, description="Estado de la tarea")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Fecha de creación")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="Fecha de última actualización")
    due_date: Optional[datetime] = Field(None, description="Fecha de vencimiento de la tarea")
    priority: int = Field(3, ge=1, le=5, description="Prioridad de la tarea (1: alta, 5: baja)")
    tags: List[str] = Field(default_factory=list, description="Etiquetas asociadas a la tarea")
    parent_id: Optional[UUID] = Field(None, description="ID de la tarea principal si es una subtarea")

class TaskCreate(BaseModel):
    title: str = Field(..., example="Comprar leche")
    description: Optional[str] = Field(None, example="Leche semidesnatada")
    due_date: Optional[datetime] = Field(None, description="Fecha de vencimiento de la tarea")
    priority: Optional[int] = Field(3, ge=1, le=5, description="Prioridad de la tarea (1: alta, 5: baja)")
    tags: Optional[List[str]] = Field(default_factory=list, description="Etiquetas asociadas a la tarea")
    parent_id: Optional[UUID] = Field(None, description="ID de la tarea principal si es una subtarea")

class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, example="Comprar leche")
    description: Optional[str] = Field(None, example="Leche semidesnatada")
    completed: Optional[bool] = Field(None, description="Estado de la tarea")
    due_date: Optional[datetime] = Field(None, description="Fecha de vencimiento de la tarea")
    priority: Optional[int] = Field(None, ge=1, le=5, description="Prioridad de la tarea (1: alta, 5: baja)")
    tags: Optional[List[str]] = Field(None, description="Etiquetas asociadas a la tarea")
    parent_id: Optional[UUID] = Field(None, description="ID de la tarea principal si es una subtarea")

# ---------------------------
# Almacenamiento en memoria por IP
# Estructura: { client_ip: { task_id: Task } }
# ---------------------------
tasks_db: Dict[str, Dict[UUID, Task]] = {}

def get_client_tasks(ip: str) -> Dict[UUID, Task]:
    """Devuelve el diccionario de tareas para una IP; si no existe, lo crea."""
    if ip not in tasks_db:
        tasks_db[ip] = {}
    return tasks_db[ip]

# ---------------------------
# Sistema de Notificaciones (opcional)
# ---------------------------
def get_notifications(tasks: List[Task]) -> List[str]:
    notifications = []
    now = datetime.utcnow()
    for task in tasks:
        if task.due_date:
            if not task.completed:
                if now > task.due_date:
                    notifications.append(f"La tarea '{task.title}' (ID: {task.id}) está vencida.")
                elif task.due_date - now < timedelta(hours=1):
                    notifications.append(f"La tarea '{task.title}' (ID: {task.id}) vence en menos de 1 hora.")
    return notifications

# ---------------------------
# Endpoints principales
# ---------------------------
@app.post("/tasks/", response_model=Task, status_code=201, dependencies=[Depends(verify_api_key)])
def create_task(task: TaskCreate, request: Request, background_tasks: BackgroundTasks):
    client_ip = request.client.host
    client_tasks = get_client_tasks(client_ip)
    
    new_task = Task(
        title=task.title,
        description=task.description,
        due_date=task.due_date,
        priority=task.priority if task.priority is not None else 3,
        tags=task.tags if task.tags is not None else [],
        parent_id=task.parent_id,
    )
    client_tasks[new_task.id] = new_task
    logger.info(f"[{client_ip}] Tarea creada: {new_task.id}")

    # Si hay fecha de vencimiento, se podría planificar una notificación (simulado)
    if new_task.due_date:
        # Por ejemplo, se podría agregar una tarea en background para enviar notificación cuando esté cerca
        background_tasks.add_task(logger.info, f"[{client_ip}] Programada notificación para la tarea {new_task.id}")

    return new_task

@app.get("/tasks/", response_model=List[Task])
def list_tasks(
    request: Request,
    completed: Optional[bool] = Query(None, description="Filtrar por estado de completado"),
    tag: Optional[str] = Query(None, description="Filtrar tareas que contengan una etiqueta"),
    priority: Optional[int] = Query(None, ge=1, le=5, description="Filtrar por prioridad"),
    parent: Optional[bool] = Query(False, description="Si es True, muestra solo tareas principales"),
    limit: int = Query(10, ge=1, description="Número máximo de tareas a devolver"),
    skip: int = Query(0, ge=0, description="Número de tareas a omitir"),
):
    client_ip = request.client.host
    client_tasks = list(get_client_tasks(client_ip).values())

    # Aplicar filtros
    if completed is not None:
        client_tasks = [t for t in client_tasks if t.completed == completed]
    if tag:
        client_tasks = [t for t in client_tasks if tag in t.tags]
    if priority is not None:
        client_tasks = [t for t in client_tasks if t.priority == priority]
    if parent:
        client_tasks = [t for t in client_tasks if t.parent_id is None]

    return client_tasks[skip: skip + limit]

@app.get("/tasks/{task_id}", response_model=Task)
def get_task(task_id: UUID = Path(..., description="ID de la tarea"), request: Request = None):
    client_ip = request.client.host
    client_tasks = get_client_tasks(client_ip)
    task = client_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarea no encontrada para esta IP")
    return task

@app.put("/tasks/{task_id}", response_model=Task, dependencies=[Depends(verify_api_key)])
def update_task(task_id: UUID, task_data: TaskCreate, request: Request):
    client_ip = request.client.host
    client_tasks = get_client_tasks(client_ip)
    task = client_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarea no encontrada para esta IP")
    # Actualización completa (excepto id y created_at)
    task.title = task_data.title
    task.description = task_data.description
    task.due_date = task_data.due_date
    task.priority = task_data.priority if task_data.priority is not None else task.priority
    task.tags = task_data.tags if task_data.tags is not None else task.tags
    task.parent_id = task_data.parent_id
    task.updated_at = datetime.utcnow()
    client_tasks[task_id] = task
    logger.info(f"[{client_ip}] Tarea actualizada: {task_id}")
    return task

@app.patch("/tasks/{task_id}", response_model=Task, dependencies=[Depends(verify_api_key)])
def partial_update_task(task_id: UUID, task_data: TaskUpdate, request: Request):
    client_ip = request.client.host
    client_tasks = get_client_tasks(client_ip)
    task = client_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarea no encontrada para esta IP")
    update_data = task_data.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(task, key, value)
    task.updated_at = datetime.utcnow()
    client_tasks[task_id] = task
    logger.info(f"[{client_ip}] Tarea actualizada parcialmente: {task_id}")
    return task

@app.delete("/tasks/{task_id}", response_model=dict, dependencies=[Depends(verify_api_key)])
def delete_task(task_id: UUID, request: Request):
    client_ip = request.client.host
    client_tasks = get_client_tasks(client_ip)
    if task_id not in client_tasks:
        raise HTTPException(status_code=404, detail="Tarea no encontrada para esta IP")
    # Eliminar también las subtareas asociadas (dentro del mismo espacio IP)
    subtasks_to_delete = [tid for tid, t in client_tasks.items() if t.parent_id == task_id]
    for subtask_id in subtasks_to_delete:
        del client_tasks[subtask_id]
        logger.info(f"[{client_ip}] Subtarea eliminada: {subtask_id}")
    del client_tasks[task_id]
    logger.info(f"[{client_ip}] Tarea eliminada: {task_id}")
    return {"message": "Tarea eliminada correctamente"}

# ---------------------------
# Endpoints para Subtareas
# ---------------------------
@app.get("/tasks/{task_id}/subtasks", response_model=List[Task])
def list_subtasks(task_id: UUID = Path(..., description="ID de la tarea principal"), request: Request = None):
    client_ip = request.client.host
    client_tasks = get_client_tasks(client_ip)
    if task_id not in client_tasks:
        raise HTTPException(status_code=404, detail="Tarea principal no encontrada para esta IP")
    subtasks = [t for t in client_tasks.values() if t.parent_id == task_id]
    return subtasks

@app.post("/tasks/{task_id}/subtasks", response_model=Task, status_code=201, dependencies=[Depends(verify_api_key)])
def create_subtask(task_id: UUID, subtask_data: TaskCreate, request: Request, background_tasks: BackgroundTasks):
    client_ip = request.client.host
    client_tasks = get_client_tasks(client_ip)
    if task_id not in client_tasks:
        raise HTTPException(status_code=404, detail="Tarea principal no encontrada para esta IP")
    subtask = Task(
        title=subtask_data.title,
        description=subtask_data.description,
        due_date=subtask_data.due_date,
        priority=subtask_data.priority if subtask_data.priority is not None else 3,
        tags=subtask_data.tags if subtask_data.tags is not None else [],
        parent_id=task_id,
    )
    client_tasks[subtask.id] = subtask
    logger.info(f"[{client_ip}] Subtarea creada: {subtask.id} para la tarea principal: {task_id}")

    if subtask.due_date:
        background_tasks.add_task(logger.info, f"[{client_ip}] Programada notificación para la subtarea {subtask.id}")
    return subtask

# ---------------------------
# Endpoint de Estadísticas
# ---------------------------
@app.get("/tasks/stats", response_model=dict)
def tasks_stats(request: Request):
    client_ip = request.client.host
    client_tasks = list(get_client_tasks(client_ip).values())
    total = len(client_tasks)
    completed = len([t for t in client_tasks if t.completed])
    pending = total - completed
    overdue = len([t for t in client_tasks if t.due_date and t.due_date < datetime.utcnow() and not t.completed])
    stats = {
        "total_tasks": total,
        "completed_tasks": completed,
        "pending_tasks": pending,
        "overdue_tasks": overdue,
    }
    return stats

# ---------------------------
# Endpoint de Notificaciones
# ---------------------------
@app.get("/tasks/notifications", response_model=List[str])
def tasks_notifications(request: Request):
    client_ip = request.client.host
    client_tasks = list(get_client_tasks(client_ip).values())
    notifications = get_notifications(client_tasks)
    return notifications
