# 검증과 압축

보존된 2026-09-19 검사 코드를 공개용 경로·CLI로 정리했습니다. 출력 JSON에는 본인의 로컬 경로가 들어갈 수 있으므로 공유할 때 필요한 수치만 추출합니다.

## 공통 카메라로 확장 결과 비교

```sh
python scripts/check_expanded_geometry.py \
  --old-model runs/house/sparse/0 --new-model runs/town/sparse/0 \
  --new-prefix s003_town_ --output runs/review
```

공통 카메라 중심으로 Umeyama 유사변환을 맞추고 잔차·이동 이상 후보를 기록합니다. 좌표는 SfM 단위이며 미터가 아닙니다. 이 비교만으로 새 구역의 실제 형상 정확도를 입증하지 않습니다. `--plot`은 선택 사항이며 별도 matplotlib 설치가 필요합니다.

## 학습 평가 PNG와 입력 사진 비교

```sh
python scripts/evaluate_training.py \
  --eval-dir runs/town/output/eval --reference-dir runs/town/dataset/images \
  --output runs/evaluation --contactsheet
```

실제 Brush 출력의 평가 이미지 디렉터리를 지정합니다. 같은 이름의 참조 사진을 찾아 RGB PNG 기반 PSNR과 비교 이미지를 만듭니다. Brush 내부의 부동소수 평가 수치와는 다른 외부 근사 지표입니다. 비율이 맞지 않는 사진을 임의로 잘라 맞추지 않습니다.

## 원본을 보존하는 압축

`npm ci --ignore-scripts`로 설치한 SplatTransform 3.4.2를 사용합니다.

```sh
python scripts/compress_model.py runs/town/output/final.ply \
  --output runs/town/scene.compressed.ply
```

실제 완성된 PLY 경로를 지정합니다. 출력·검증 JSON·변환 로그가 이미 있으면 덮어쓰지 않습니다. 외장 볼륨을 강제하려면 `--require-mount MOUNT_PATH`를 추가합니다. 원본 해시·Gaussian 개수·SH 차수·파일 크기와 압축 chunk 수치를 확인합니다. 압축된 표현에는 양자화 손실이 있으며, 시각적 비교는 별도로 수행합니다.

CI에서는 생성한 네 점짜리 PLY를 실제 고정 버전 인코더에 통과시킵니다. 사용자의 대형 모델은 다운로드하지 않습니다.
