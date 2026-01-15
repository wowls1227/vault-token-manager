import requests
import time

API_BASE = "http://127.0.0.1:5001"

CREATE_TOKEN_URL = f"{API_BASE}/api/token/create"
GET_DATA_URL = f"{API_BASE}/api/data"

HEADERS_JSON = {
    "Content-Type": "application/json"
}

# 테스트 설정
TOKEN_COUNT = 100
SLEEP_BETWEEN_REQUESTS = 0.05  # Vault 보호용 (필요 시 조절)

created_tokens = []


def create_token(index: int) -> str | None:
    payload = {
        "name": f"test-token-{index}",
        "permissions": {
            "create": True,
            "read": True,
            "update": False,
            "delete": False,
            "list": True
        }
    }

    try:
        resp = requests.post(
            CREATE_TOKEN_URL,
            headers=HEADERS_JSON,
            json=payload,
            timeout=10
        )

        if resp.status_code == 200:
            token = resp.json().get("token")
            print(f"[CREATE OK] {index:03d} token={token[:]}")
            return token
        else:
            print(
                f"[CREATE FAIL] {index:03d} "
                f"status={resp.status_code} body={resp.text}"
            )
            return None

    except Exception as e:
        print(f"[CREATE ERROR] {index:03d} error={e}")
        return None


def call_get_data(index: int, token: str):
    headers = {
        "Token-Header": token
    }

    try:
        resp = requests.get(
            GET_DATA_URL,
            headers=headers,
            timeout=10
        )

        if resp.status_code == 200:
            data = resp.json()["data"]
            print(
                f"[GET OK] {index:03d} "
                f"user={data.get('user')} ttl={data.get('ttl')}"
            )
        else:
            print(
                f"[GET FAIL] {index:03d} "
                f"status={resp.status_code} body={resp.text}"
            )

    except Exception as e:
        print(f"[GET ERROR] {index:03d} error={e}")


def main():
    print("=== STEP 1: 토큰 생성 시작 ===")

    for i in range(TOKEN_COUNT):
        token = create_token(i)
        if token:
            created_tokens.append(token)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    print(f"\n생성 완료: {len(created_tokens)} / {TOKEN_COUNT}")

    print("\n=== STEP 2: get_data 호출 시작 ===")

    for i, token in enumerate(created_tokens):
        call_get_data(i, token)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    print("\n=== TEST COMPLETE ===")


if __name__ == "__main__":
    main()