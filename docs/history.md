# 개발 이력 · Retrospective history

이 저장소는 **이미 진행한 개발을 2026-09-21에 공개용 이슈·브랜치·PR로 정리한 기록**이다. 실제 실험 날짜와 GitHub에 정리한 날짜를 구분한다. 과거 날짜로 커밋을 만들거나 당시 리뷰·CI가 존재한 것처럼 꾸미지 않는다.

코드가 남은 부분은 공개 가능한 실행 코드로 정리하고, 결과만 남은 부분은 당시의 측정 자료로 기록했다. 공개 준비 과정에서 로컬 경로·배포 설정을 일반화한 코드는 과거 파일의 바이트 단위 스냅샷이 아니다. 새로 수행하는 CI는 공개용 코드의 현재 검사이며 과거 GPU 복원을 다시 수행한 증거가 아니다.

| 단계 | 실제 작업일 | 이슈·PR 주제 | 판단 근거 |
| --- | --- | --- | --- |
| 1 | 09-14 | Mac 환경·샘플 시험 | 131장으로 전체 처리 흐름 확인 |
| 2 | 09-19 | 집 복원 파이프라인 | 389장, 구간 추출·단계 기록·원본 보존 |
| 3 | 09-19 | 동네 확장 | 682장 통합 정합 후 새 학습 |
| 4 | 09-19 | 압축·웹 뷰어 | 점 수 유지,74.03% 용량 감소,6개 시점 |
| 5 | 09-19 / 09-21 | 공개 제공·캐시 | 공개 자산 경계와HTTP 재검증 |
| 6 | 09-21 | 시연·포트폴리오 자료 | 실제3D 조작GIF, 입력 프레임·시간 기록 |
| 7 | 09-21 | 독립 주소·iframe | 허용 목록 프록시, 전송·삽입 정책 검증 |

순서는 기능의 의존성과 설명 흐름에 맞춘 것이다. 같은 날짜에 겹쳐 진행한 작업을 분 단위의 정확한 개발 순서로 주장하지 않는다. 공개 정리에서 추가한 일반화·검사는 각 PR에 별도로 적는다.

## 읽는 방법

각 이슈는 당시 해결하려던 문제와 완료 조건을 설명하고, 연결된 PR은 공개 코드·문서 변화와 검증 근거를 설명한다. `Closes #…`로 이슈와 PR을 연결하며, 병합 시점은 GitHub가 기록한 실제 현재 시점을 사용한다. 다른 사람이 검토한 것처럼 리뷰 계정이나 승인 기록을 만들지 않는다.

- [측정 결과](results.md): 입력·정합·학습·압축 수치와 한계
- [실행 시간 JSON](../evidence/reconstruction-timing.json): 최종 확장 실행의 단계별 시간
- [출처·기여 범위](provenance.md): 외부 도구, 샘플,미디어 권리
- [구조](architecture.md): 복원과 공개 전달의 경계
- [7단계 공개 계획](history-plan.json): 이슈·PR 작성에 사용한 구조화된 설명

## 재현 범위

원본 촬영 영상과 전체 모델은 Git 저장소에 포함하지 않는다. 따라서 CI는 이 마을을 다시 학습하지 않으며, 공개된 코드의 단위 검사·구문·설정 검증을 수행한다. 자체 영상으로 처리 경로를 실행할 수 있지만 하드웨어·버전·입력·확률적 학습에 따라 결과와 시간은 달라진다. 측정 증거를 결과 품질이나 서비스 가동률의 보증으로 해석하지 않는다.

## GitHub 공개 기록

| 단계 | 문제 정의 | 코드·문서 변경 |
| --- | --- | --- |
| 1 | [Issue #1](https://github.com/BlueStylo/drone-gaussian-pipeline/issues/1) | [PR #8](https://github.com/BlueStylo/drone-gaussian-pipeline/pull/8) |
| 2 | [Issue #2](https://github.com/BlueStylo/drone-gaussian-pipeline/issues/2) | [PR #9](https://github.com/BlueStylo/drone-gaussian-pipeline/pull/9) |
| 3 | [Issue #3](https://github.com/BlueStylo/drone-gaussian-pipeline/issues/3) | [PR #10](https://github.com/BlueStylo/drone-gaussian-pipeline/pull/10) |
| 4 | [Issue #4](https://github.com/BlueStylo/drone-gaussian-pipeline/issues/4) | [PR #11](https://github.com/BlueStylo/drone-gaussian-pipeline/pull/11) |
| 5 | [Issue #5](https://github.com/BlueStylo/drone-gaussian-pipeline/issues/5) | [PR #12](https://github.com/BlueStylo/drone-gaussian-pipeline/pull/12) |
| 6 | [Issue #6](https://github.com/BlueStylo/drone-gaussian-pipeline/issues/6) | 현재 단계 PR 작성 중 |
