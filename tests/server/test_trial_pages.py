from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from deploy.native_preview import Ledger, portal_app


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.text = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)

    def field(self):
        return next(attrs for tag, attrs in self.tags if tag == 'input')


@pytest.mark.parametrize('runtime, version, other', [('js', 'JS', 'py'), ('py', 'Python', 'js')])
def test_login_page_and_error_are_shared_and_preserve_literal_input(tmp_path, runtime, version, other):
    config = {'runtime': runtime, 'revision': 'page-test', 'title': '<Trial & test>',
              'tenants': {'alice': {'invite': 'only-alice'}}}
    ledger = Ledger(tmp_path / 'ledger', total_usd=500, user_usd=50)
    with TestClient(portal_app(config, ledger), base_url='https://trial.test') as client:
        normal = client.get('/login')
        assert normal.status_code == 200 and normal.headers['cache-control'] == 'no-store'
        page = Page(normal.text)
        assert f'试用 / {version} 版' in ''.join(page.text)
        assert '<Trial & test>' in page.text
        assert '500 美元' not in normal.text
        assert normal.text.count('50 美元额度') == 1
        assert 'aria-invalid' not in page.field()
        links = [attrs for tag, attrs in page.tags if tag == 'a']
        assert {'href': '/', 'rel': 'noreferrer'} in links
        assert any(attrs.get('href') == f'/{other}/' for attrs in links)
        assert page.field()['aria-describedby'] == 'invite-error invite-note'
        malicious = '\"><script>alert(1)</script>{error}&'
        response = client.post('/login', data={'invite': '  ' + malicious + '  '})
        assert response.status_code == 401 and response.headers['cache-control'] == 'no-store'
        error = Page(response.text)
        assert error.field()['value'] == malicious
        assert error.field()['aria-invalid'] == 'true'
        assert '这个邀请码不对，请核对后再贴一次。' in error.text
        assert sum(tag == 'script' for tag, attrs in error.tags) == 1
        result = client.post('/login', data={'invite': ' \n only-alice \t '}, follow_redirects=False)
        assert result.status_code == 303
        assert result.headers['location'] == '/'
        assert '__Host-argus_preview_session=' in result.headers['set-cookie']
        assert client.get('/trial/budget').json()['remaining_usd'] == 50
