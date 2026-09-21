# 출처와 기여 범위 · Provenance

이 프로젝트는 기존 복원·렌더링 도구를 연결해 실제 촬영 영상을 3D로 만들고 공개 웹에 전달한 구현이다. Gaussian Splatting 학습 알고리즘이나 렌더링 엔진을 처음부터 만든 프로젝트로 소개하지 않는다.

직접 구성한 부분은 입력 구간·프레임 추출 설정, 단계별 실행과 시간 기록, 집에서 동네로의 정합 확장, 수치·압축·원본 보존 검사, 시점·조작 UI, 공개 제공 경계, HTTP 캐시·Range 검증, Cloudflare 프록시와 포트폴리오 전달 자료다.

## 사용 도구

아래 버전은 2026-09-14~21의 로컬 실행 기록과 패키지 설정에서 확인한 값이다. 최신 버전이라는 의미는 아니다. 각 도구의 소스·바이너리와 라이선스는 원 프로젝트를 따른다.

| 도구 | 기록된 버전 | 역할 | 공식 출처 |
| --- | --- | --- | --- |
| FFmpeg | 9.0.1 | 영상 정보·프레임 추출 | [FFmpeg](https://ffmpeg.org/) |
| COLMAP | 4.2.0 | 특징·매칭·카메라 정합·왜곡 보정 | [COLMAP](https://github.com/colmap/colmap) |
| Brush | 0.3.0 | Apple Silicon Metal Gaussian 학습 | [공식 릴리스](https://github.com/ArthurBrussee/brush/releases/tag/v0.3.0) |
| PlayCanvas | 2.22.1 | 브라우저 Gaussian 렌더링 | [Engine](https://github.com/playcanvas/engine) |
| Splat Transform | 3.4.2 | 웹용 PLY 변환·압축 | [splat-transform](https://github.com/playcanvas/splat-transform) |
| Wrangler | 4.135.0 | Cloudflare Worker 검증·배포 | [workers-sdk](https://github.com/cloudflare/workers-sdk) |

공개 코드의 MIT License가 외부 도구나 모델·미디어까지 일괄 적용되는 것은 아니다. PlayCanvas 엔진을 직접 호스팅하면 해당 MIT 라이선스 고지도 함께 제공한다. 배포 바이너리는 이 저장소에 포함하지 않는다.

## 촬영·샘플·미디어

집·동네의 입력 영상과 이 저장소의 프레임·복원 시연은 프로젝트 소유자가 촬영·제작한 결과다. 미디어는 별도 권리 고지 (`../assets/LICENSE.md` — 후속 공개 단계에서 추가)를 따른다. 위치 좌표·개인 경로·원본 로그는 공개용 증거에서 제외했다. JPEG에 EXIF/GPS/XMP가 없는지 점검했으며 공개용으로 준비된 원래 픽셀을 그대로 복사했다.

선행 환경 시험은 nickesc / N. Escobar의 [Photogrammetry Video Instructions](https://github.com/nickesc/PhotogrammetryVideoInstructions) 신발 촬영 샘플을 사용했다. 2026-09-14 원본 안내에서 받은 영상으로 로컬 복원을 수행했고, 작성자의 완성 모델을 입력으로 쓰지 않았다. 당시 별도 LICENSE 파일을 확인하지 못했으므로 이 저장소에는 해당 영상·사진·생성 모델을 재배포하지 않고 출처와 측정 수치만 기록한다.

## 기록의 성격

`evidence/`는 원래 실험·배포 보고서에서 공개 가능한 수치만 선별한 자료다. 원본 영상·전체 로그를 포함하는 독립 재현 데이터셋은 아니다. 로컬 절대 경로, 계정 정보, 비공개 서비스 주소, GPS를 제거했다. 사진·모델의 품질을 수치 검증만으로 보장하지 않으며 브라우저 확인과 한계를 함께 기록했다.

개발은 2026-09-14~21에 진행했고, GitHub의 이슈·브랜치·PR은 2026-09-21 공개 정리 시점에 재구성했다. 당시 PR·리뷰가 이미 존재했던 것처럼 날짜나 참여자를 만들지 않는다. 개발 이력 (`history.md` — 후속 공개 단계에서 추가)
