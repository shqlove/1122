from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pytest
from dialogue_tool.media_server import MediaServer


def test_only_registered_file_and_valid_ranges_are_served(tmp_path):
    file=tmp_path/'video.mp4'
    file.write_bytes(b'0123456789')
    server=MediaServer()
    try:
        url=server.register(file)
        with urlopen(Request(url,headers={'Range':'bytes=2-5'})) as response:
            assert response.status==206
            assert response.headers['Content-Range']=='bytes 2-5/10'
            assert response.read()==b'2345'
        with urlopen(Request(url,headers={'Range':'bytes=-3'})) as response:
            assert response.read()==b'789'
        with pytest.raises(HTTPError) as error:
            urlopen(url.rsplit('/',1)[0]+'/etc/passwd')
        assert error.value.code==404
        with pytest.raises(HTTPError) as error:
            urlopen(Request(url,headers={'Range':'bytes=100-'}))
        assert error.value.code==416
    finally:
        server.server.shutdown()
        server.server.server_close()
