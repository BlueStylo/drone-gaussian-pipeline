# Third-party tools

This repository contains original orchestration and viewer code. External
engines are installed separately and retain their upstream licenses.

| Tool | Role | Source / terms |
| --- | --- | --- |
| FFmpeg | Frame extraction | https://ffmpeg.org/legal.html — terms depend on build configuration |
| COLMAP | Camera estimation / sparse reconstruction | https://github.com/colmap/colmap |
| Brush | Gaussian training on Apple Metal | https://github.com/ArthurBrussee/brush |
| PlayCanvas 2.22.1 | Browser rendering | https://github.com/playcanvas/engine — MIT |
| SplatTransform 3.4.2 | PLY compression | https://github.com/playcanvas/splat-transform — MIT |
| NumPy / Pillow | Numeric and image validation | See installed distributions and upstream license files |

`prepare:viewer` copies the installed PlayCanvas bundle and its LICENSE into
ignored runtime files. Retain that license when deploying the prepared viewer.
No FFmpeg, COLMAP, Brush binary, raw third-party sample video, or trained model
is committed here. Dependency lockfiles record the installed package versions.
