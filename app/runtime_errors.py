"""Safe, session-only diagnostics: never retain exception text or user content."""
from dataclasses import dataclass
import errno


@dataclass(frozen=True)
class RuntimeIssue:
    code: str
    message: str


MESSAGES = {
    "out_of_memory": "显卡可用显存不足，请关闭占用显存的软件后重试；声音未恢复时可点击“重新加载声音”。",
    "gpu_failure": "显卡计算发生异常，需要重新准备声音；若反复出现，请检查显卡驱动或重启电脑。",
    "gpu_unavailable": "未检测到可用的 NVIDIA 显卡，请检查驱动；当前不会转用在线服务。",
    "missing_model": "本地模型文件不完整，请运行下载脚本校验已有文件。",
    "startup_timeout": "声音准备超过等待时间，模型进程已结束；请稍后点击“重新加载声音”。",
    "worker_timeout": "模型生成长时间没有响应，本次任务已停止，正在重新准备声音。",
    "worker_crashed": "模型进程意外退出，本次任务已停止，正在重新准备声音。",
    "worker_unavailable": "声音暂未就绪，请等待准备完成，或点击“重新加载声音”。",
    "worker_cleanup": "旧模型进程尚未成功退出，已阻止再次加载，以免重复占用显存；请退出程序后重开。",
    "worker_supervision": "无法建立模型进程保护，请重新打开程序；如仍失败，请检查系统对进程创建的限制。",
    "worker_protocol": "模型进程通信异常，本次任务已停止，正在重新准备声音。",
    "generation_failed": "语音生成或模型准备失败，请稍后重新加载声音；也可尝试缩短文字。",
    "dependencies": "本地运行依赖不完整，请运行准备脚本检查环境；不会自动转用在线服务。",
    "generation_limit": "单段声音生成达到模型上限，无法确认完整；请缩短这段文字后重试。",
    "cache_limit": "单段声音触及模型缓存边界，无法确认完整；请缩短这段文字后重试。",
    "sample_rate": "模型返回的声音采样率不受支持，本次任务已停止。",
    "invalid_audio": "模型返回了无效声音，本次任务已停止；请重试或缩短文字。",
    "audio_unavailable": "暂时没有可用的播放设备，请连接耳机或扬声器后重试；MP3 导出仍可使用。",
    "disk_full": "保存目录空间不足，请释放空间或选择其他磁盘后重试。",
    "save_permission": "无法写入保存目录，可能没有权限、磁盘只读或文件被占用；请选择其他目录。",
    "save_unavailable": "保存目录或磁盘已不可用，请重新连接磁盘或选择其他目录。",
    "file_exists": "导出文件已存在，请换一个文件名；不会覆盖原文件。",
    "mp3_encoder": "MP3 编码失败，请检查音频运行依赖；未完成的文件不会作为成功结果保存。",
    "save_io": "保存音频时发生读写错误，请检查目标磁盘或选择其他目录。",
    "export_cleanup": "未能清理未完成的 MP3，请检查你选择的目标文件并手动删除残留。",
    "unexpected": "本次处理出现异常。可在设置页复制不含正文和路径的诊断摘要，再重新加载声音或重开程序。",
}


def describe_error(error, *, exporting=False):
    code = getattr(error, "code", None)
    if code not in MESSAGES:
        if isinstance(error, ImportError):
            code = "dependencies"
        elif type(error).__name__ == "AudioOutputError":
            code = "audio_unavailable"
        elif isinstance(error, FileExistsError):
            code = "file_exists"
        elif exporting and isinstance(error, OSError):
            number, windows = getattr(error, "errno", None), getattr(error, "winerror", None)
            if number == errno.ENOSPC or windows in {39, 112}:
                code = "disk_full"
            elif isinstance(error, PermissionError) or windows in {5, 19, 32, 33}:
                code = "save_permission"
            elif isinstance(error, FileNotFoundError) or windows in {2, 3, 15, 21}:
                code = "save_unavailable"
            else:
                code = "save_io"
        else:
            code = "unexpected"
    return RuntimeIssue(code, MESSAGES[code])
