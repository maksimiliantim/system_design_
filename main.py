# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient
import redis
import os
import uuid
import json

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv(
    'DATABASE_URL', 'postgresql://myuser:mypassword@db:5432/myapp_db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'super-secret'
app.config['JSON_AS_ASCII'] = False  # Для корректного отображения кириллицы

db = SQLAlchemy(app)
jwt = JWTManager(app)

mongo_client = MongoClient("mongodb://mongo:27017/")
mongo_db = mongo_client["budgeting_system"]
mongo_categories = mongo_db["categories"]

redis_client = redis.StrictRedis(host='redis', port=6379, db=0)

class User(db.Model):
    __tablename__ = 'users'  # Явно указываем имя таблицы
    id = db.Column(db.String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

class BudgetItem(db.Model):
    __tablename__ = 'budget_items'  # Явно указываем имя таблицы
    id = db.Column(db.String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
    description = db.Column(db.Text)
    amount = db.Column(db.Numeric(10, 2), default=0)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

@app.route('/register', methods=['POST'])
def register():
    username = request.json.get('username')
    password = request.json.get('password')
    if User.query.filter_by(username=username).first():
        return jsonify({"message": "Пользователь уже существует"}), 400
    password_hash = generate_password_hash(password)
    user = User(id=str(uuid.uuid4()), username=username,
                password_hash=password_hash)
    db.session.add(user)
    db.session.commit()
    return jsonify({"id": user.id, "username": user.username}), 201

@app.route('/login', methods=['POST'])
def login():
    username = request.json.get('username')
    password = request.json.get('password')
    user = User.query.filter_by(username=username).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"message": "Неверные учетные данные"}), 401
    access_token = create_access_token(identity=user.id)
    return jsonify(access_token=access_token)

@app.route('/categories', methods=['POST'])
@jwt_required()
def create_category():
    name = request.json.get('name')
    if mongo_categories.find_one({"name": name}):
        return jsonify({"message": "Категория уже существует"}), 400
    mongo_categories.insert_one({"name": name})
    return jsonify({"message": "Категория создана"}), 201

@app.route('/categories', methods=['GET'])
@jwt_required()
def get_categories():
    categories = list(mongo_categories.find({}, {"_id": 0}))
    return jsonify(categories), 200

@app.route('/categories/<name>', methods=['DELETE'])
@jwt_required()
def delete_category(name):
    category = mongo_categories.find_one({"name": name})
    if not category:
        return jsonify({"message": "Категория не найдена"}), 404
    mongo_categories.delete_one({"name": name})
    return jsonify({"message": "Категория успешно удалена"}), 200

@app.route('/budget', methods=['POST'])
@jwt_required()
def create_budget_item():
    user_id = get_jwt_identity()
    user = User.query.filter_by(id=user_id).first()
    if not user:
        return jsonify({"message": "Пользователь не найден"}), 404
    description = request.json.get('description')
    amount = request.json.get('amount', 0)
    item = BudgetItem(id=str(uuid.uuid4()), user_id=user.id,
                      description=description, amount=amount)
    db.session.add(item)
    db.session.commit()
    # Удаляем из кэша Redis
    redis_client.delete(str(item.id))  # Преобразуем item.id в строку
    return jsonify({
        "id": item.id,
        "user": user.username,
        "description": item.description,
        "amount": float(item.amount)
    }), 201

@app.route('/budget', methods=['GET'])
@jwt_required()
def get_budget_items():
    user_id = get_jwt_identity()
    user = User.query.filter_by(id=user_id).first()
    items = BudgetItem.query.filter_by(user_id=user.id).all()
    result = []
    for item in items:
        result.append({
            "id": item.id,
            "user": user.username,
            "description": item.description,
            "amount": float(item.amount)
        })
    return jsonify(result), 200

@app.route('/budget/<item_id>', methods=['GET'])
@jwt_required()
def get_budget_item(item_id):
    item_id_str = str(item_id)
    cached_item = redis_client.get(item_id_str)
    if cached_item:
        return jsonify(json.loads(cached_item))
    user_id = get_jwt_identity()
    user = User.query.filter_by(id=user_id).first()
    item = BudgetItem.query.filter_by(id=item_id_str, user_id=user.id).first()
    if item:
        item_data = {
            "id": item.id,
            "user": user.username,
            "description": item.description,
            "amount": float(item.amount)
        }
        redis_client.set(item_id_str, json.dumps(item_data), ex=3600)
        return jsonify(item_data), 200
    return jsonify({"message": "Запись не найдена"}), 404

@app.route('/budget/<item_id>', methods=['PUT'])
@jwt_required()
def edit_budget_item(item_id):
    item_id_str = str(item_id)
    user_id = get_jwt_identity()
    user = User.query.filter_by(id=user_id).first()
    item = BudgetItem.query.filter_by(id=item_id_str, user_id=user.id).first()
    if not item:
        return jsonify({"message": "Запись не найдена или не принадлежит пользователю"}), 404
    item.description = request.json.get('description', item.description)
    item.amount = request.json.get('amount', item.amount)
    db.session.commit()
    # Обновляем кэш Redis
    redis_client.delete(item_id_str)
    return jsonify({
        "id": item.id,
        "user": user.username,
        "description": item.description,
        "amount": float(item.amount)
    }), 200

@app.route('/budget/<item_id>', methods=['DELETE'])
@jwt_required()
def delete_budget_item(item_id):
    item_id_str = str(item_id)
    user_id = get_jwt_identity()
    user = User.query.filter_by(id=user_id).first()
    item = BudgetItem.query.filter_by(id=item_id_str, user_id=user.id).first()
    if item:
        db.session.delete(item)
        db.session.commit()
        redis_client.delete(item_id_str)  # Преобразуем item_id в строку
        return jsonify({"message": "Запись успешно удалена"}), 200
    return jsonify({"message": "Запись не найдена"}), 404

@app.route('/budget/<item_id>/add', methods=['POST'])
@jwt_required()
def add_amount(item_id):
    item_id_str = str(item_id)
    user_id = get_jwt_identity()
    user = User.query.filter_by(id=user_id).first()
    item = BudgetItem.query.filter_by(id=item_id_str, user_id=user.id).first()
    if not item:
        return jsonify({"message": "Запись не найдена или не принадлежит пользователю"}), 404
    amount_to_add = request.json.get('amount')
    if amount_to_add is None:
        return jsonify({"message": "Не указана сумма для добавления"}), 400
    item.amount += amount_to_add
    db.session.commit()
    # Обновляем кэш Redis
    redis_client.delete(item_id_str)
    return jsonify({
        "id": item.id,
        "user": user.username,
        "description": item.description,
        "amount": float(item.amount)
    }), 200

@app.route('/budget/<item_id>/subtract', methods=['POST'])
@jwt_required()
def subtract_amount(item_id):
    item_id_str = str(item_id)
    user_id = get_jwt_identity()
    user = User.query.filter_by(id=user_id).first()
    item = BudgetItem.query.filter_by(id=item_id_str, user_id=user.id).first()
    if not item:
        return jsonify({"message": "Запись не найдена или не принадлежит пользователю"}), 404
    amount_to_subtract = request.json.get('amount')
    if amount_to_subtract is None:
        return jsonify({"message": "Не указана сумма для вычитания"}), 400
    item.amount -= amount_to_subtract
    db.session.commit()

    redis_client.delete(item_id_str)
    return jsonify({
        "id": item.id,
        "user": user.username,
        "description": item.description,
        "amount": float(item.amount)
    }), 200

if __name__ == "__main__":
    db.create_all()
    app.run(host="0.0.0.0", port=8080, debug=True)