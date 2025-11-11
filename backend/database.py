import os
from azure.cosmos import CosmosClient, PartitionKey
from dotenv import load_dotenv

# 🔹 .env 로드
load_dotenv()

URL = os.getenv("COSMOS_URL")
KEY = os.getenv("COSMOS_KEY")

# 🔹 Cosmos DB 클라이언트 생성
client = CosmosClient(URL, credential=KEY)

# 🔹 데이터베이스 생성/접근
database = client.create_database_if_not_exists(id="SnaplogDB")

# 🔹 Users 컨테이너 (partition_key를 username으로 변경)
user_container = database.create_container_if_not_exists(
    id="Users",
    partition_key=PartitionKey(path="/username")
)

# 🔹 Diaries 컨테이너 (userId 기준)
diary_container = database.create_container_if_not_exists(
    id="Diaries",
    partition_key=PartitionKey(path="/userId")
)