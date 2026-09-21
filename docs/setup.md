> 공개 이력 재구성 진행 중: 이 문서는 완성될 실행 흐름을 설명하며, 현재 단계에서 아직 추가되지 않은 코드는 뒤의 PR에서 공개합니다.

# 로컬 실행 · Setup

먼저 [공개 3D 데모](https://yangdong-3d.yangdong-3d-public-proxy.workers.dev/)를 확인할 수 있다. 직접 복원하려면 본인 소유의 영상, 여유 저장 공간, FFmpeg·COLMAP·Brush가 필요하다. 원본 드론 영상·학습 모델·도구 바이너리는 이 저장소에 포함하지 않는다.

## 확인된 환경

원래 실험은 Apple M1 Pro / 32GB 통합 메모리에서 실행했다. FFmpeg 9.0.1, COLMAP 4.2.0, Brush 0.3.0, PlayCanvas 2.22.1, Splat Transform 3.4.2를 기록했다. COLMAP 특징·정합은 CPU, Brush 학습은 Metal을 사용했다. macOS 환경의 실행 기록이며 Linux·Windows 전체 파이프라인을 검증했다는 의미는 아니다.

Homebrew가 준비된 Mac에서는 다음으로 FFmpeg와 COLMAP을 설치할 수 있다. Homebrew가 설치하는 현재 버전은 위 기록과 달라질 수 있으므로 실행 전 버전을 남긴다. [COLMAP 공식 설치 안내](https://colmap.github.io/install.html)

```sh
brew install ffmpeg colmap
ffmpeg -version
colmap -h
```

Brush는 [0.3.0 공식 릴리스](https://github.com/ArthurBrussee/brush/releases/tag/v0.3.0)의 Apple Silicon 패키지를 별도로 받는다. 압축을 풀고 실제 `brush_app` 실행 파일의 위치를 확인한다. 당시 공식 아카이브 SHA-256은 `65b2631398c839be3c1d4d7160fe2326389dec87830aac0710985e6690a1048c`로 대조했다. 다른 릴리스를 사용하면 해당 릴리스의 체크섬·CLI를 다시 확인한다.

## 저장소 의존성

Python 3.13과 Node.js 22.22 이상 / npm을 준비한 뒤 저장소 루트에서 실행한다. 복원 실행기 자체는 Python 표준 라이브러리를 사용하며, 수치·평가 이미지 검사는 NumPy·Pillow를 사용한다. Node 패키지는 잠금 파일로 고정한다.

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
npm ci
npm run check
```

`npm run check`는 공개 코드의 오프라인 검사다. 영상 다운로드·GPU 학습·운영 서버 접속을 수행하는 벤치마크가 아니다.

## 1. 입력 구간 설정과 집 복원

`configs/house.example.json`을 별도 JSON으로 복사해 본인 영상의 경로·시작·끝 구간을 지정한다. `path`는 **JSON 파일이 있는 폴더 기준 상대 경로**다. 아래는 형태를 설명하는 짧은 예시이며 공개 결과를 만든 원본 영상이 들어 있다는 의미는 아니다.

```json
{
  "segments": [
    {"path": "../inputs/orbit.mp4", "start": 0, "end": 30, "label": "orbit"}
  ]
}
```

```sh
python -m pipeline.reconstruct \
  --manifest configs/house.example.json \
  --run-dir runs/house \
  --stop-after sfm

python -m pipeline.reconstruct \
  --run-dir runs/house \
  --mode train \
  --brush /path/to/brush_app \
  --steps 6000
```

`/path/to/brush_app`은 실제 설치 위치로 교체한다. 첫 명령 전에 예시 입력 경로도 실제 파일로 바꿔야 한다. `--fps`, `--resolution`, `--features`, `--threads` 등의 지원 옵션은 `python -m pipeline.reconstruct --help`로 확인한다. 외장 SSD를 사용할 때는 새 실행에 `--required-mount`를 지정해 예상한 볼륨이 연결돼 있는지 검사할 수 있다.

프레임은 입력 구간에서 일정 간격으로 추출한다. 집 실험에서는 1fps·최대 변 1,600px를 사용했고, 학습 이미지 최대 변은 1,280px였다. 입력을 확인한 뒤 짧은 구간부터 실행해 정합 결과를 점검한다.

## 2. 새 촬영분으로 확장

`configs/extension.example.json`에서 완료된 `source_run`, 새 `output_run`, 추가 영상 한 구간을 지정한다. 경로 기준은 이 JSON의 위치다. 기존 실행 폴더를 출력 대상으로 덮어쓰지 않는다. 정합 결과를 확인한 뒤 별도 학습을 시작한다.

```sh
python -m pipeline.extend_reconstruction \
  --config configs/extension.example.json

python -m pipeline.extend_reconstruction \
  --run-dir runs/expanded \
  --train \
  --brush /path/to/brush_app \
  --steps 12000
```

추가 영상과 기존 촬영분 사이에 공통으로 보이는 건물·지형이 있어야 하나의 장면으로 연결하기 쉽다. 원래 실험의 389+293장과 12,000단계는 관측된 설정이며 모든 촬영에 적합한 최적값은 아니다. 완료된 이미지·COLMAP 정합을 재사용하는 기능과 학습 optimizer 재개는 구분한다.

## 3. 압축과 검사

`INPUT.ply`, `OUTPUT.compressed.ply`를 실제 경로로 교체한다. 보관용 학습 원본과 웹용 파일을 분리한다.

```sh
python scripts/compress_model.py INPUT.ply --output OUTPUT.compressed.ply
```

선택 검사 도구는 카메라 정합 기하 비교(`scripts/check_expanded_geometry.py`)와 평가 렌더 비교(`scripts/evaluate_training.py`)다. 각 `--help`에서 필수 경로를 확인한다. 기하 시각화의 `--plot`은 Matplotlib이 추가로 필요하다. 수치·헤더 검사를 통과해도 가려진 면이나 식물의 시각 품질이 보장되는 것은 아니다.

## 4. 로컬 뷰어

```sh
npm run prepare:viewer
python -m http.server 8080 --bind 127.0.0.1 --directory viewer
```

서버 실행 전에 본인의 압축 모델을 `viewer/model.ply`에 놓고, 같은 모델 좌표계의 `viewer/scene.json`을 준비한다. `prepare:viewer`는 설치된 PlayCanvas 엔진과 라이선스만 복사하며 모델을 다운로드하지 않는다. [뷰어 설정](web-viewer.md)에서 장면과 시점 형식을 확인한다.

공개 모델용 시점을 다른 모델에 그대로 쓰면 카메라가 장면 밖을 볼 수 있다. 로컬 Python 서버는 표시 확인용이며 운영 캐시·접근 경계 검증을 대신하지 않는다.

## 5. 공개 배포

[배포 문서](deployment.md)를 따라 공개 자산만 제공하는 원본과 Worker를 준비한다. Worker의 `ASSET_BASE_URL`은 끝에 `/`가 붙은 HTTPS 자산 디렉터리, `ALLOWED_FRAME_ORIGIN`은 경로가 없는 단일 HTTPS 출처다. 본인 환경에 맞게 설정하며 기존 서비스의 계정·주소를 공개 저장소에 넣지 않는다.

공개 뷰어의 프레임과 모델에는 별도 미디어 권리 (`../assets/LICENSE.md` — 후속 공개 단계에서 추가)가 적용된다. 이 저장소의 실행 예시는 프로젝트 소유자의 모델·촬영물을 자유롭게 재배포할 권한을 부여하지 않는다.
