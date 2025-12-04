import aiofiles
import argparse
import asyncio
import datetime
import logging
import os

from pathlib import Path
from aiohttp import web
from environs import Env


env = Env()
env.read_env()

CHUNK_SIZE = 500 * 1024


def setup_logging(enable: bool):
    if enable:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.disable(logging.CRITICAL)


async def archive(request):
    config = request.app['config']
    catalog_path = config['photos_path']
    delay_enabled = config['delay_enabled']

    archive_hash = request.match_info.get('archive_hash', '')
    photos_path = Path(catalog_path) / archive_hash

    if not photos_path.exists():
        raise web.HTTPNotFound(text=f'Архив не существует или был удален', content_type='text/html')

    files = []
    for photo in photos_path.rglob('*'):
        if photo.is_file():
            path = photo.relative_to(photos_path)
            files.append(str(path))

    process = await asyncio.create_subprocess_exec(
    'zip', '-', *files,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    cwd=str(photos_path)
    )

    response = web.StreamResponse()

    response.headers['Content-Type'] = 'application/zip'
    response.headers['Content-Disposition'] = f'attachment; filename="photos.zip"'

    await response.prepare(request)
    
    count_chunk = 0
        
    try:
        while True:
            chunk = await process.stdout.read(CHUNK_SIZE)
            
            if not chunk:
                break

            if delay_enabled:
                await asyncio.sleep(1)

            count_chunk += 1
            logging.info(f'Sending archive chunk № {count_chunk}')
        
            try:
                await response.write(chunk)
            except ConnectionResetError:
                raise asyncio.CancelledError('Connection reset by client')

        await response.write_eof()

    except asyncio.CancelledError:
        logging.info('Download was interrupted by client')
        raise
        
    except IndexError as e:
        logging.error(f'Error: {e}')
        raise web.HTTPInternalServerError()

    except SystemExit:
        raise

    finally:
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()


        if process.returncode != 0:
            stderr = (await process.stderr.read()).decode()
            raise RuntimeError(f'Ошибка: {stderr}')

    return response

async def handle_index_page(request):
    async with aiofiles.open('index.html', mode='r', encoding='utf-8') as index_file:
        index_contents = await index_file.read()
    return web.Response(text=index_contents, content_type='text/html')


def main():
    delay_default = env.bool('SPEED_DELAY', False)
    photos_default = env.str('PHOTOS_PATH', 'test_photos')

    parser = argparse.ArgumentParser(description='Архиватор фотографий')
    
    parser.add_argument(
        '--photos-path',
        default=photos_default,
        help='Путь к папке с фотографиями (по умолчанию: test_photos)'
    )
    
    parser.add_argument(
        '--log',
        default=env.bool('LOGS', False),
        help='Включить логирование (по умолчанию: выключено)'
    )

    parser.add_argument(
        '--delay',
        default=delay_default,
        help='Включить задержку скорости скачивания (по умолчанию: выключено)'
    )

    args = parser.parse_args()

    setup_logging(args.log)

    logging.info(f' Путь к фото: {args.photos_path}')
    logging.info(f' Логирование: {args.log}')
    logging.info(f' Задержка скорости: {args.delay}')

    app = web.Application()
    app['config'] = {
        'photos_path': args.photos_path,
        'delay_enabled': args.delay
    }
    app.add_routes([
    web.get('/', handle_index_page),
    web.get('/archive/{archive_hash}/', archive),
    ])
    web.run_app(app)


if __name__ == '__main__':
    main()



