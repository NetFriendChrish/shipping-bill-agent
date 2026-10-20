"""打包提交代码，不包含虚拟环境、密钥和运行缓存。"""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent.parent


def main():
    files = [ROOT / name for name in ('README.md', '使用说明.md', '补充思考.md', 'requirements.txt', '.env.example', '.gitignore', '.gitattributes', 'setup.cmd', 'start.cmd')]
    for folder in ('shipping_agent', 'web', 'scripts', 'examples', 'samples'):
        files.extend(p for p in (ROOT / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    target = ROOT / 'dist' / 'shipping-bill-agent-submission.zip'
    target.parent.mkdir(exist_ok=True)
    with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
        for path in sorted(set(files)):
            archive.write(path, 'shipping-bill-agent/' + path.relative_to(ROOT).as_posix())
    with ZipFile(target) as archive:
        if archive.testzip() is not None: raise ValueError('提交包校验失败')
        print(f'{target}\n共{len(archive.namelist())}个文件，{target.stat().st_size}字节')


if __name__ == '__main__': main()
