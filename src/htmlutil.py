def escape(value):
    """Escape text for use in HTML content or a quoted attribute value."""
    return (str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;').replace("'", '&#39;'))
