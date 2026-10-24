# 第三方组件

本仓库不包含模型权重、Python 安装程序或依赖的二进制文件。它们按固定版本和校验值独立下载。各组件仍受其上游许可约束，项目 MIT 许可不覆盖第三方组件。

- [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)：Qwen3-TTS-12Hz-1.7B-CustomVoice，模型文件以其模型卡和附带许可为准。
- [faster-qwen3-tts](https://github.com/andimarafioti/faster-qwen3-tts)：0.5.4，CUDA 图加速与流式生成；通过依赖安装，并未复制源码。
- [qwen-tts-hf](https://github.com/andimarafioti/Qwen3-TTS/tree/distribution/qwen-tts-hf)：0.1.1.post1，适配当前锁定的 Transformers 版本。
- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)：5.2.2；发行包声明 CC0 1.0 Universal。程序使用其随包字体与界面资源，许可文件保留在安装后的包内。
- [AudioTSM](https://github.com/Muges/audiotsm)：0.1.2，MIT，流式 WSOLA 语速处理。
- [PyTorch](https://pytorch.org/) / torchaudio：2.7.1 CUDA 12.8，各自附带许可。
- Python、NumPy、sounddevice、soundfile 及完整依赖：见 `scripts/requirements-offline.txt` 和安装包附带的许可文件。

目前发布物只包含本项目源码与下载元数据。如果未来将第三方运行环境或字体等资源一并打包，需要重新检查相应的再分发要求并包含必要声明。
