import argparse
import json
from pathlib import Path

from .service import Service
from .settings import settings


def main():
    parser = argparse.ArgumentParser(description='对话视频工作台（Windows 11 / macOS，CPU 优先）')
    subs = parser.add_subparsers(dest='command', required=True)
    subs.add_parser('desktop', help='启动桌面窗口')
    subs.add_parser('doctor', help='仅检查环境，不提交生成任务')
    subs.add_parser('h3-check', help='检查优云智算 H3 专属密钥及可用积分，不生成视频')
    subs.add_parser('h3-set-key', help='通过隐藏输入在本机保存 H3 专属密钥，不经过聊天')
    subs.add_parser('asr-set-key', help='本机隐藏输入阿里云百炼 Key')
    subs.add_parser('asr-status', help='检查语音识别配置，不发起收费调用')
    asr = subs.add_parser('transcribe', help='上传所选音轨到阿里云识别，消耗百炼额度')
    asr.add_argument('file', type=Path)
    asr.add_argument('--start', type=float, default=0)
    asr.add_argument('--end', type=float)
    create = subs.add_parser('import-video', help='建立项目，原视频保持原位')
    create.add_argument('video', type=Path)
    analyze = subs.add_parser('analyze', help='逐帧扫描并通过 Codex 识别候选组，会使用 Codex 额度')
    analyze.add_argument('project')
    analyze.add_argument('--start', type=float, default=0)
    analyze.add_argument('--end', type=float)
    analyze.add_argument('--scan-only', action='store_true')
    subs.add_parser('workflow-schema', help='输出 AI 可用的工作台工具 JSON Schema')
    workflow = subs.add_parser('workflow', help='通过结构化接口操作项目，不依赖电脑操作')
    workflow.add_argument('project')
    workflow.add_argument('action')
    workflow.add_argument('--arguments', default='{}', help='JSON 参数；使用 - 从标准输入读取')
    args = parser.parse_args()
    if args.command == 'desktop':
        from .desktop import launch
        launch()
        return
    if args.command == 'h3-set-key':
        import getpass
        from .compshare import H3Client
        from .generation import CanvasError
        key = getpass.getpass('粘贴视频工作台 sk-ml- API Key（不显示输入）: ').strip()
        if not key.startswith('sk-ml-') or any(c.isspace() for c in key):
            parser.error('须使用视频工作台专属 sk-ml- 密钥')
        try:
            H3Client(settings()).save_key(key)
        except CanvasError as exc:
            parser.exit(1, str(exc) + '\n')
        print('H3 密钥已保存到本机；运行 h3-check 可检查连接与积分。')
        return
    if args.command == 'asr-set-key':
        import getpass
        from .aliyun_asr import configure, ASRError
        region = input('地域：1 北京（默认），2 新加坡：').strip()
        workspace = input('业务空间 Workspace ID（留空使用传统域名）：').strip()
        key = getpass.getpass('百炼 API Key（隐藏输入，不经过聊天）：')
        try:
            configure(settings(), key, 'singapore' if region == '2' else 'beijing', workspace)
        except ASRError as exc:
            parser.exit(1, str(exc) + '\n')
        print('阿里云语音识别配置已保存；尚未发起识别。重启工作台后可在聊天中要求识别成片或附件。')
        return
    if args.command in ('asr-status', 'transcribe'):
        from .aliyun_asr import status, transcribe, ASRError
        config = settings()
        try:
            result = status(config) if args.command == 'asr-status' else transcribe(
                config, args.file, Path(config['data']) / 'asr-results', args.start, args.end)
        except (ASRError, OSError) as exc:
            parser.exit(1, str(exc) + '\n')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    service = Service(settings())
    if args.command == 'h3-check':
        from .compshare import CompShareWorkflow
        from .generation import CanvasError
        try:
            result = CompShareWorkflow(service).credit_balance(0)
        except CanvasError as exc:
            parser.exit(1, str(exc) + '\n')
    elif args.command == 'doctor':
        from .desktop import DesktopAPI
        result = DesktopAPI(service).check_environment()
    elif args.command == 'import-video':
        result = service.projects.create(args.video)
    elif args.command == 'workflow-schema':
        from .workflow_api import dynamic_tools
        result = dynamic_tools()
    elif args.command == 'workflow':
        import sys
        from .workflow_api import WorkflowAPI
        payload = sys.stdin.read() if args.arguments == '-' else args.arguments
        result = WorkflowAPI(service).execute(args.project, args.action, json.loads(payload))
    else:
        result = service.analyze(args.project, args.start, args.end, scan_only=args.scan_only)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
