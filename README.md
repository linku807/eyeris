# Eyeris 시선 의사소통 보드

웹캠 시선 추적으로 화면의 9~16개 의사소통 버튼을 선택하는 Python 데스크톱 앱입니다. 화면에 시선 커서를 표시하고, 가장 가까운 버튼을 강조합니다. 양쪽 눈을 함께 감으면 강조된 버튼을 실행합니다. `Space` 키로 현재 포커스된 보드 버튼을 실행하는 보조 입력도 제공합니다.

의사소통 보드의 **천지인 키보드** 칸에 시선을 약 1.1초 두면 키보드가 열립니다. 입력 키와 말하기, Discord 전송, 보드 복귀 키도 같은 방식으로 선택하므로 키보드 화면에서는 깜빡임이나 마우스를 쓰지 않아도 됩니다. 활성 키의 테두리와 진행 표시줄로 체류 시간을 확인할 수 있습니다. 키에서 시선을 옮기면 다시 선택할 수 있습니다.

## 실행 환경

- Windows 10/11, Python 3.10~3.12
- 시선 입력을 사용하려면 연결된 웹캠
- `tkinter`가 포함된 Python 배포본

이 저장소에서는 웹캠을 열거나 앱을 실행하지 않았습니다. 최초 실행 시 상단의 **시선 보정**을 누르고, 노란 점이 이동할 때 각 점을 바라보면 화면 좌표를 보정합니다. 조명, 카메라 위치, 안경 등에 따라 추적 정확도가 달라질 수 있으므로 사용자별 보정이 필요합니다.

## Windows 설치 및 실행

PowerShell에서 프로젝트 폴더로 이동한 뒤:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

앱은 전체 화면으로 열립니다. `Esc`로 종료하고 `F11`로 전체 화면을 전환합니다.

## 버튼 편집

`config.json`의 `grid.columns`, `grid.rows`와 `buttons` 항목을 편집합니다. 버튼은 9~16개여야 하고, 격자 칸 수 이하여야 합니다. 각 버튼의 `action.type`은 `sound`, `discord`, `script` 중 하나입니다.

보드에는 키보드로 이동하는 내장 칸도 표시됩니다. 설정한 버튼이 격자를 모두 채우면 내장 칸을 위해 한 행이 자동으로 추가됩니다.

## 천지인 입력

자음 키는 한 번 입력할 때마다 그룹의 다음 글자로 순환합니다. 예를 들어 `ㄱ · ㅋ · ㄲ` 키를 다시 선택하면 다음 자음이 입력됩니다. 다시 선택하려면 잠시 다른 곳으로 시선을 옮겼다가 돌아오세요. 같은 자음 키를 5초 넘게 지난 뒤 선택하면 그룹의 첫 자음부터 시작합니다.

모음은 `ㅣ`, `ㆍ`, `ㅡ` 조합으로 입력합니다. 예를 들어 `ㅣ` 다음 `ㆍ`는 `ㅏ`, `ㅡ` 다음 `ㆍ`는 `ㅗ`가 됩니다. 받침이 있는 음절 뒤에 모음을 입력하면 받침을 다음 음절의 초성으로 옮깁니다. 입력 문장은 **말하기** 또는 **Discord 전송**으로 출력할 수 있습니다. Discord 출력은 위의 전역 웹훅 설정을 사용합니다.

### 소리 또는 음성

`text`가 있으면 시스템의 음성 엔진으로 문장을 읽습니다. 사용할 wav/mp3 파일이 있다면 상대 경로나 절대 경로를 `file`에 지정할 수 있습니다.

```json
{"label": "안녕하세요", "color": "#2563eb", "action": {"type": "sound", "text": "안녕하세요"}}
```

### Discord 웹훅

웹훅 URL은 `config.json`의 전역 `discord.webhook_url`에 입력합니다. 버튼별 `action.webhook_url`을 지정하면 전역 주소 대신 해당 주소를 사용합니다. 환경 변수 `EYE_DISCORD_WEBHOOK`도 대체 설정으로 지원합니다. 메시지는 버튼의 `message`에 적습니다.

```json
"discord": {"webhook_url": "https://discord.com/api/webhooks/..."}
```

Discord 버튼은 `buttons` 목록에 다음과 같이 둡니다.

```json
{"label": "도움 요청", "color": "#dc2626", "action": {"type": "discord", "message": "도움이 필요합니다."}}
```

웹훅 URL은 비밀 주소이므로 `config.json`을 공유하거나 저장소에 공개할 때 주의하세요.

### 사용자 Python 스크립트

스크립트는 `action.path`에 지정합니다. 실행 시 다음 형태의 JSON이 `--payload` 인자로 전달됩니다.

```json
{"label": "사용자 버튼", "action": {"type": "script", "path": "scripts/example.py"}}
```

```python
# scripts/example.py
import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("--payload", required=True)
args = parser.parse_args()
payload = json.loads(args.payload)
print(f"실행된 버튼: {payload['label']}")
```

사용자 스크립트는 현재 사용자 권한으로 별도 Python 프로세스에서 실행됩니다. 시간이 오래 걸리거나 실패해도 화면 입력이 멈추지 않으며, 최대 실행 시간은 20초입니다.

## 구현 메모

- `eyeris/gaze.py`: MediaPipe Face Mesh와 OpenCV 카메라 입력, 눈 감김 감지, 시선 보정
- `eyeris/dwell.py`: 다른 화면에서도 재사용할 수 있는 시선 체류 선택 상태기
- `eyeris/cheonjiin.py`: 천지인 자모 키 매핑과 한글 초성·중성·종성 조합 모델
- `eyeris/keyboard.py`: UI와 분리된 천지인 키보드 레이아웃과 입력 세션
- `eyeris/app.py`: Tkinter 전체 화면 보드, 키보드, 시선 커서
- `eyeris/actions.py`: Discord 전송, 음성/파일 재생, 사용자 스크립트
- 웹캠 접근은 앱을 실행할 때만 시작합니다. 카메라가 없거나 패키지가 설치되지 않은 경우 상태 표시줄에 오류가 나타납니다.
