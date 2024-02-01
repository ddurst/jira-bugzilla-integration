from textwrap import dedent

from jbi.jira.md2jira import convert


def test_titles():
    converted = convert(
        dedent(
            """
    # top

    ## subtitle

    * a list
    * an item
    """
        )
    )
    assert converted == dedent(
        """
    h1. top

    h2. subtitle

    * a list
    * an item"""
    )


def test_bold_and_italic():
    converted = convert(
        dedent(
            """
    this sentence __has__ **bold** and _has_ *italic*
    """
        )
    )
    assert converted == convert(
        dedent(
            """
    this sentence *has* *bold* and _has_ _italic_
    """
        )
    )


def test_strikethrough():
    converted = convert(
        dedent(
            """
    this was ~~wrong~~
    """
        )
    )
    assert converted == convert(
        dedent(
            """
    this was -wrong-
    """
        )
    )


def test_links():
    converted = convert(
        dedent(
            """
    there is a [link](http://site.com)
    """
        )
    )
    assert converted == convert(
        dedent(
            """
    there is a [link|http://site.com]
    """
        )
    )


def test_multiline_codeblocks():
    converted = convert(
        dedent(
            """
    ```py
    # some code
    print("hola munda")
    ```

    ```
    some code
    ```
    """
        )
    )
    assert converted == dedent(
        """
    {code:py}
    # some code
    print("hola munda")
    {code}

    {code}
    some code
    {code}"""
    )


def test_quotes():
    converted = convert(
        dedent(
            """
    Some text

    > A previous *text*
    >
    my answer
    """
        )
    )
    assert converted == dedent(
        """
    Some text

    {quote}
    A previous _text_

    {quote}
    my answer"""
    )
