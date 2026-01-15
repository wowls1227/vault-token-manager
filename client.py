import requests
import os

VAULT_ADDR = os.getenv('VAULT_ADDR', 'http://127.0.0.1:8200')
TEST_SERVER = 'http://localhost:5001'

# 1. 발급받은 토큰으로 Test Server API 호출
def call_api(token):
    response = requests.get(
        f'{TEST_SERVER}/api/data',
        headers={'X-Vault-Token': token}
    )
    return response.json()

if __name__ == '__main__':
    # 토큰 발급
    token = 'hvs.CAESIAoOIopIExgFvs0WA_HbJbWs7aKedtyZA8ANrTia3wIEGh4KHGh2cy40T09qUEFjWDBoY0dCcEdQdDdCbjZ1d20'
    print(f"발급된 토큰: {token}")
    
    # API 호출
    result = call_api(token)
    print(f"API 응답: {result}")