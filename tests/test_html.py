from htmlutil import escape
from provisioning import Provisioning


def test_escape_covers_markup_and_both_quote_kinds():
    assert escape('<a href="x">&\'') == '&lt;a href=&quot;x&quot;&gt;&amp;&#39;'
    assert escape(12) == '12'


def test_setup_form_escapes_network_names_device_name_and_error():
    form = Provisioning('Jack"><b>')._get_html_form(
        ['"><script>alert(1)</script>', 'Home'], error='<img src=x onerror=alert(2)>')
    assert '<script>alert' not in form
    assert '<img src=x' not in form
    assert '"><b>' not in form
    assert '<option value="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;">' in form
    assert '<option value="Home">' in form
    assert 'value="Jack&quot;&gt;&lt;b&gt;"' in form


def test_setup_form_starts_with_the_current_device_name():
    assert 'value="PorchLights"' in Provisioning('PorchLights')._get_html_form([])


def test_setup_form_falls_back_to_the_generated_name():
    assert 'value="PicoLights-A1B2C3"' in Provisioning()._get_html_form([])
