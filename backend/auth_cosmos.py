"""
CosmosDB 연결 및 사용자 인증 관리
"""
import os
import jwt
import bcrypt
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify
from azure.cosmos import CosmosClient, exceptions
from dotenv import load_dotenv

# 환경변수 로드
load_dotenv()

# CosmosDB 설정
COSMOS_ENDPOINT = os.getenv("COSMOS_ENDPOINT")
COSMOS_KEY = os.getenv("COSMOS_KEY")
COSMOS_DATABASE = os.getenv("COSMOS_DATABASE")
COSMOS_USERS_CONTAINER = os.getenv("COSMOS_USERS_CONTAINER", "Users")
COSMOS_DIARIES_CONTAINER = os.getenv("COSMOS_DIARIES_CONTAINER", "Diaries")

# JWT 설정
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-this-secret-key")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRATION_HOURS = int(os.getenv("JWT_EXPIRATION_HOURS", "24"))

# CosmosDB 클라이언트 초기화
cosmos_client = None
database = None
users_container = None
diaries_container = None

def init_cosmos_db():
    """CosmosDB 초기화 및 컨테이너 생성"""
    global cosmos_client, database, users_container, diaries_container
    
    try:
        # 클라이언트 생성
        cosmos_client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
        
        # 데이터베이스 생성 또는 가져오기
        database = cosmos_client.create_database_if_not_exists(id=COSMOS_DATABASE)
        print(f"✅ 데이터베이스 연결 성공: {COSMOS_DATABASE}")
        
        # Users 컨테이너 생성 (파티션 키: /email)
        # Serverless 계정이므로 offer_throughput 제거!
        users_container = database.create_container_if_not_exists(
            id=COSMOS_USERS_CONTAINER,
            partition_key={"paths": ["/email"], "kind": "Hash"}
        )
        print(f"✅ Users 컨테이너 생성 완료: {COSMOS_USERS_CONTAINER}")
        
        # Diaries 컨테이너 생성 (파티션 키: /userId)
        # Serverless 계정이므로 offer_throughput 제거!
        diaries_container = database.create_container_if_not_exists(
            id=COSMOS_DIARIES_CONTAINER,
            partition_key={"paths": ["/userId"], "kind": "Hash"}
        )
        print(f"✅ Diaries 컨테이너 생성 완료: {COSMOS_DIARIES_CONTAINER}")
        
        return True
        
    except Exception as e:
        print(f"❌ CosmosDB 초기화 실패: {e}")
        return False


# ============ 비밀번호 해싱 ============

def hash_password(password: str) -> str:
    """비밀번호를 bcrypt로 해싱"""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')


def verify_password(password: str, hashed: str) -> bool:
    """비밀번호 검증"""
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))


# ============ JWT 토큰 생성/검증 ============

def create_token(user_id: str, email: str) -> str:
    """JWT 토큰 생성"""
    payload = {
        'user_id': user_id,
        'email': email,
        'exp': datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS),
        'iat': datetime.utcnow()
    }
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token


def verify_token(token: str) -> dict | None:
    """JWT 토큰 검증"""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None  # 토큰 만료
    except jwt.InvalidTokenError:
        return None  # 유효하지 않은 토큰


# ============ 인증 데코레이터 ============

def login_required(f):
    """로그인이 필요한 API에 사용하는 데코레이터"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Authorization 헤더에서 토큰 추출
        auth_header = request.headers.get('Authorization')
        
        if not auth_header:
            return jsonify({'ok': False, 'error': 'missing_token', 'message': '로그인이 필요합니다.'}), 401
        
        # "Bearer {token}" 형식에서 토큰만 추출
        try:
            token = auth_header.split(' ')[1]
        except IndexError:
            return jsonify({'ok': False, 'error': 'invalid_header', 'message': '잘못된 Authorization 헤더 형식입니다.'}), 401
        
        # 토큰 검증
        payload = verify_token(token)
        if not payload:
            return jsonify({'ok': False, 'error': 'invalid_token', 'message': '유효하지 않거나 만료된 토큰입니다.'}), 401
        
        # request에 사용자 정보 추가
        request.user_id = payload['user_id']
        request.user_email = payload['email']
        
        return f(*args, **kwargs)
    
    return decorated_function


# ============ 사용자 관리 함수 ============

def create_user(email: str, password: str, name: str = None) -> dict:
    """새 사용자 생성"""
    try:
        # 이메일 중복 확인
        existing_users = list(users_container.query_items(
            query="SELECT * FROM c WHERE c.email = @email",
            parameters=[{"name": "@email", "value": email}],
            enable_cross_partition_query=True
        ))
        
        if existing_users:
            return {'ok': False, 'error': 'email_exists', 'message': '이미 사용 중인 이메일입니다.'}
        
        # 사용자 ID 생성 (타임스탬프 + 이메일 해시)
        user_id = f"user_{int(datetime.utcnow().timestamp())}_{hash(email) % 10000}"
        
        # 비밀번호 해싱
        hashed_password = hash_password(password)
        
        # 사용자 데이터 생성
        user_data = {
            'id': user_id,
            'email': email,
            'password': hashed_password,
            'name': name or email.split('@')[0],
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        }
        
        # CosmosDB에 저장
        users_container.create_item(body=user_data)
        
        # 토큰 생성
        token = create_token(user_id, email)
        
        return {
            'ok': True,
            'user': {
                'id': user_id,
                'email': email,
                'name': user_data['name']
            },
            'token': token
        }
        
    except Exception as e:
        print(f"❌ 사용자 생성 실패: {e}")
        return {'ok': False, 'error': 'server_error', 'message': str(e)}


def authenticate_user(email: str, password: str) -> dict:
    """사용자 로그인 인증"""
    try:
        # 이메일로 사용자 조회
        users = list(users_container.query_items(
            query="SELECT * FROM c WHERE c.email = @email",
            parameters=[{"name": "@email", "value": email}],
            enable_cross_partition_query=True
        ))
        
        if not users:
            return {'ok': False, 'error': 'user_not_found', 'message': '존재하지 않는 사용자입니다.'}
        
        user = users[0]
        
        # 비밀번호 검증
        if not verify_password(password, user['password']):
            return {'ok': False, 'error': 'wrong_password', 'message': '비밀번호가 일치하지 않습니다.'}
        
        # 토큰 생성
        token = create_token(user['id'], user['email'])
        
        return {
            'ok': True,
            'user': {
                'id': user['id'],
                'email': user['email'],
                'name': user.get('name', email.split('@')[0])
            },
            'token': token
        }
        
    except Exception as e:
        print(f"❌ 로그인 실패: {e}")
        return {'ok': False, 'error': 'server_error', 'message': str(e)}


def get_user_by_id(user_id: str) -> dict | None:
    """사용자 ID로 사용자 정보 조회"""
    try:
        users = list(users_container.query_items(
            query="SELECT * FROM c WHERE c.id = @user_id",
            parameters=[{"name": "@user_id", "value": user_id}],
            enable_cross_partition_query=True
        ))
        
        if users:
            user = users[0]
            # 비밀번호는 제외하고 반환
            return {
                'id': user['id'],
                'email': user['email'],
                'name': user.get('name', ''),
                'created_at': user.get('created_at', '')
            }
        return None
        
    except Exception as e:
        print(f"❌ 사용자 조회 실패: {e}")
        return None


# ============ 일기 관리 함수 ============

def save_diary(user_id: str, diary_text: str, images: list = None, metadata: dict = None) -> dict:
    """사용자 일기 저장"""
    try:
        # 일기 ID 생성
        diary_id = f"diary_{int(datetime.utcnow().timestamp())}_{hash(user_id) % 10000}"
        
        # 일기 데이터 생성
        diary_data = {
            'id': diary_id,
            'userId': user_id,
            'text': diary_text,
            'images': images or [],
            'metadata': metadata or {},
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        }
        
        # CosmosDB에 저장
        diaries_container.create_item(body=diary_data)
        
        return {'ok': True, 'diary_id': diary_id, 'message': '일기가 저장되었습니다.'}
        
    except Exception as e:
        print(f"❌ 일기 저장 실패: {e}")
        return {'ok': False, 'error': 'server_error', 'message': str(e)}


def get_user_diaries(user_id: str, limit: int = 50) -> list:
    """사용자의 일기 목록 조회 (최신순)"""
    try:
        diaries = list(diaries_container.query_items(
            query="SELECT * FROM c WHERE c.userId = @user_id ORDER BY c.created_at DESC",
            parameters=[{"name": "@user_id", "value": user_id}],
            enable_cross_partition_query=False,
            max_item_count=limit
        ))
        
        return diaries
        
    except Exception as e:
        print(f"❌ 일기 조회 실패: {e}")
        return []


def get_diary_by_id(diary_id: str, user_id: str) -> dict | None:
    """특정 일기 조회 (본인 확인)"""
    try:
        diaries = list(diaries_container.query_items(
            query="SELECT * FROM c WHERE c.id = @diary_id AND c.userId = @user_id",
            parameters=[
                {"name": "@diary_id", "value": diary_id},
                {"name": "@user_id", "value": user_id}
            ],
            enable_cross_partition_query=False
        ))
        
        return diaries[0] if diaries else None
        
    except Exception as e:
        print(f"❌ 일기 조회 실패: {e}")
        return None


def delete_diary(diary_id: str, user_id: str) -> dict:
    """일기 삭제 (본인만 가능)"""
    try:
        # 일기 존재 여부 및 소유권 확인
        diary = get_diary_by_id(diary_id, user_id)
        
        if not diary:
            return {'ok': False, 'error': 'not_found', 'message': '일기를 찾을 수 없습니다.'}
        
        # 삭제
        diaries_container.delete_item(item=diary_id, partition_key=user_id)
        
        return {'ok': True, 'message': '일기가 삭제되었습니다.'}
        
    except Exception as e:
        print(f"❌ 일기 삭제 실패: {e}")
        return {'ok': False, 'error': 'server_error', 'message': str(e)}