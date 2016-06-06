#!/usr/bin/env python3
# -*- coding: utf-8 -*-


"""CSS-HTML-JS-Minify.

StandAlone Async single-file cross-platform no-dependency Minifier for the Web.
"""


import itertools
import sys

from argparse import ArgumentParser
from datetime import datetime
from multiprocessing import cpu_count, Pool
from time import sleep

import os
import re

from hashlib import sha1
from shutil import make_archive, rmtree

from css_html_js_minify import __version__, __url__, __source__
from css_html_js_minify.variables import *
from functools import partial

from subprocess import getoutput
from io import StringIO  # pure-Python StringIO supports unicode.

from anglerfish import (check_folder, walk2list, make_post_exec_msg,
                        make_logger, check_encoding, set_process_name,
                        set_single_instance)


start_time = datetime.now()


###############################################################################
# CSS minify


def _compile_props(props_text, grouped=False):
    """Take a list of props and prepare them."""
    props, prefixes = [], "-webkit-,-khtml-,-epub-,-moz-,-ms-,-o-,".split(",")
    for propline in props_text.strip().lower().splitlines():
        props += [pre + pro for pro in propline.split(" ") for pre in prefixes]
    props = filter(lambda line: not line.startswith('#'), props)
    if not grouped:
        props = list(filter(None, props))
        return props, [0]*len(props)
    final_props, groups, g_id = [], [], 0
    for prop in props:
        if prop.strip():
            final_props.append(prop)
            groups.append(g_id)
        else:
            g_id += 1
    return final_props, groups


def _prioritify(line_of_css, css_props_text_as_list):
    """Return args priority, priority is integer and smaller means higher."""
    sorted_css_properties, groups_by_alphabetic_order = css_props_text_as_list
    priority_integer, group_integer = 9999, 0
    for css_property in sorted_css_properties:
        if css_property.lower() == line_of_css.split(":")[0].lower().strip():
            priority_integer = sorted_css_properties.index(css_property)
            group_integer = groups_by_alphabetic_order[priority_integer]
            break
    return priority_integer, group_integer


def _props_grouper(props, pgs):
    """Return groups for properties."""
    if not props:
        return props
    # props = sorted([
        # _ if _.strip().endswith(";")
        # and not _.strip().endswith("*/") and not _.strip().endswith("/*")
        # else _.rstrip() + ";\n" for _ in props])
    props_pg = zip(map(lambda prop: _prioritify(prop, pgs), props), props)
    props_pg = sorted(props_pg, key=lambda item: item[0][1])
    props_by_groups = map(
        lambda item: list(item[1]),
        itertools.groupby(props_pg, key=lambda item: item[0][1]))
    props_by_groups = map(lambda item: sorted(
        item, key=lambda item: item[0][0]), props_by_groups)
    props = []
    for group in props_by_groups:
        group = map(lambda item: item[1], group)
        props += group
        props += ['\n']
    props.pop()
    return props


def sort_properties(css_unsorted_string):
    """CSS Property Sorter Function.

    This function will read buffer argument, split it to a list by lines,
    sort it by defined rule, and return sorted buffer if it's CSS property.
    This function depends on '_prioritify' function.
    """
    log.debug("Alphabetically Sorting all CSS / SCSS Properties.")
    css_pgs = _compile_props(CSS_PROPS_TEXT, grouped=False)  # Do Not Group.
    pattern = re.compile(r'(.*?{\r?\n?)(.*?)(}.*?)|(.*)',
                         re.DOTALL + re.MULTILINE)
    matched_patterns = pattern.findall(css_unsorted_string)
    sorted_patterns, sorted_buffer = [], css_unsorted_string
    re_prop = re.compile(r'((?:.*?)(?:;)(?:.*?\n)|(?:.*))',
                         re.DOTALL + re.MULTILINE)
    if len(matched_patterns) != 0:
        for matched_groups in matched_patterns:
            sorted_patterns += matched_groups[0].splitlines(True)
            props = map(lambda line: line.lstrip('\n'),
                        re_prop.findall(matched_groups[1]))
            props = list(filter(lambda line: line.strip('\n '), props))
            props = _props_grouper(props, css_pgs)
            sorted_patterns += props
            sorted_patterns += matched_groups[2].splitlines(True)
            sorted_patterns += matched_groups[3].splitlines(True)
        sorted_buffer = ''.join(sorted_patterns)
    return sorted_buffer


def remove_comments(css):
    """Remove all CSS comment blocks."""
    log.debug("Removing all Comments.")
    iemac, preserve = False, False
    comment_start = css.find("/*")
    while comment_start >= 0:  # Preserve comments that look like `/*!...*/`.
        # Slicing is used to make sure we dont get an IndexError.
        preserve = css[comment_start + 2:comment_start + 3] == "!"
        comment_end = css.find("*/", comment_start + 2)
        if comment_end < 0:
            if not preserve:
                css = css[:comment_start]
                break
        elif comment_end >= (comment_start + 2):
            if css[comment_end - 1] == "\\":
                # This is an IE Mac-specific comment; leave this one and the
                # following one alone.
                comment_start = comment_end + 2
                iemac = True
            elif iemac:
                comment_start = comment_end + 2
                iemac = False
            elif not preserve:
                css = css[:comment_start] + css[comment_end + 2:]
            else:
                comment_start = comment_end + 2
        comment_start = css.find("/*", comment_start)
    return css


def remove_unnecessary_whitespace(css):
    """Remove unnecessary whitespace characters."""
    log.debug("Removing all unnecessary white spaces.")

    def pseudoclasscolon(css):
        """Prevent 'p :link' from becoming 'p:link'.

        Translates 'p :link' into 'p ___PSEUDOCLASSCOLON___link'.
        This is translated back again later.
        """
        regex = re.compile(r"(^|\})(([^\{\:])+\:)+([^\{]*\{)")
        match = regex.search(css)
        while match:
            css = ''.join([
                css[:match.start()],
                match.group().replace(":", "___PSEUDOCLASSCOLON___"),
                css[match.end():]])
            match = regex.search(css)
        return css

    css = pseudoclasscolon(css)
    # Remove spaces from before things.
    css = re.sub(r"\s+([!{};:>\(\)\],])", r"\1", css)
    # If there is a `@charset`, then only allow one, and move to beginning.
    css = re.sub(r"^(.*)(@charset \"[^\"]*\";)", r"\2\1", css)
    css = re.sub(r"^(\s*@charset [^;]+;\s*)+", r"\1", css)
    # Put the space back in for a few cases, such as `@media screen` and
    # `(-webkit-min-device-pixel-ratio:0)`.
    css = re.sub(r"\band\(", "and (", css)
    # Put the colons back.
    css = css.replace('___PSEUDOCLASSCOLON___', ':')
    # Remove spaces from after things.
    css = re.sub(r"([!{}:;>\(\[,])\s+", r"\1", css)
    return css


def remove_unnecessary_semicolons(css):
    """Remove unnecessary semicolons."""
    log.debug("Removing all unnecessary semicolons.")
    return re.sub(r";+\}", "}", css)


def remove_empty_rules(css):
    """Remove empty rules."""
    log.debug("Removing all unnecessary empty rules.")
    return re.sub(r"[^\}\{]+\{\}", "", css)


def normalize_rgb_colors_to_hex(css):
    """Convert `rgb(51,102,153)` to `#336699`."""
    log.debug("Converting all rgba to hexadecimal color values.")
    regex = re.compile(r"rgb\s*\(\s*([0-9,\s]+)\s*\)")
    match = regex.search(css)
    while match:
        colors = map(lambda s: s.strip(), match.group(1).split(","))
        hexcolor = '#%.2x%.2x%.2x' % tuple(map(int, colors))
        css = css.replace(match.group(), hexcolor)
        match = regex.search(css)
    return css


def condense_zero_units(css):
    """Replace `0(px, em, %, etc)` with `0`."""
    log.debug("Condensing all zeroes on values.")
    return re.sub(r"([\s:])(0)(px|em|%|in|q|ch|cm|mm|pc|pt|ex|rem|s|ms|"
                  r"deg|grad|rad|turn|vw|vh|vmin|vmax|fr)", r"\1\2", css)


def condense_multidimensional_zeros(css):
    """Replace `:0 0 0 0;`, `:0 0 0;` etc. with `:0;`."""
    log.debug("Condensing all multidimensional zeroes on values.")
    return css.replace(":0 0 0 0;", ":0;").replace(
        ":0 0 0;", ":0;").replace(":0 0;", ":0;").replace(
            "background-position:0;", "background-position:0 0;").replace(
                "transform-origin:0;", "transform-origin:0 0;")


def condense_floating_points(css):
    """Replace `0.6` with `.6` where possible."""
    log.debug("Condensing all floating point values.")
    return re.sub(r"(:|\s)0+\.(\d+)", r"\1.\2", css)


def condense_hex_colors(css):
    """Shorten colors from #AABBCC to #ABC where possible."""
    log.debug("Condensing all hexadecimal color values.")
    regex = re.compile(
        r"""([^\"'=\s])(\s*)#([0-9a-f])([0-9a-f])([0-9a-f])"""
        r"""([0-9a-f])([0-9a-f])([0-9a-f])""", re.I | re.S)
    match = regex.search(css)
    while match:
        first = match.group(3) + match.group(5) + match.group(7)
        second = match.group(4) + match.group(6) + match.group(8)
        if first.lower() == second.lower():
            css = css.replace(
                match.group(), match.group(1) + match.group(2) + '#' + first)
            match = regex.search(css, match.end() - 3)
        else:
            match = regex.search(css, match.end())
    return css


def condense_whitespace(css):
    """Condense multiple adjacent whitespace characters into one."""
    log.debug("Condensing all unnecessary white spaces.")
    return re.sub(r"\s+", " ", css)


def condense_semicolons(css):
    """Condense multiple adjacent semicolon characters into one."""
    log.debug("Condensing all unnecessary multiple adjacent semicolons.")
    return re.sub(r";;+", ";", css)


def wrap_css_lines(css, line_length=80):
    """Wrap the lines of the given CSS to an approximate length."""
    log.debug("Wrapping lines to ~{0} max line lenght.".format(line_length))
    lines, line_start = [], 0
    for i, char in enumerate(css):
        # Its safe to break after } characters.
        if char == '}' and (i - line_start >= line_length):
            lines.append(css[line_start:i + 1])
            line_start = i + 1
    if line_start < len(css):
        lines.append(css[line_start:])
    return '\n'.join(lines)


def condense_font_weight(css):
    """Condense multiple font weights into shorter integer equals."""
    log.debug("Condensing font weights on values.")
    return css.replace('font-weight:normal;', 'font-weight:400;').replace(
        'font-weight:bold;', 'font-weight:700;')


def condense_std_named_colors(css):
    """Condense named color values to shorter replacement using HEX."""
    log.debug("Condensing standard named color values.")
    for color_name, color_hexa in iter(tuple({
        ':aqua;': ':#0ff;', ':blue;': ':#00f;',
            ':fuchsia;': ':#f0f;', ':yellow;': ':#ff0;'}.items())):
        css = css.replace(color_name, color_hexa)
    return css


def condense_xtra_named_colors(css):
    """Condense named color values to shorter replacement using HEX."""
    log.debug("Condensing extended named color values.")
    for k, v in iter(tuple(EXTENDED_NAMED_COLORS.items())):
        same_color_but_rgb = 'rgb({0},{1},{2})'.format(v[0], v[1], v[2])
        if len(k) > len(same_color_but_rgb):
            css = css.replace(k, same_color_but_rgb)
    return css


def remove_url_quotes(css):
    """Fix for url() does not need quotes."""
    log.debug("Removing quotes from url.")
    return re.sub(r'url\((["\'])([^)]*)\1\)', r'url(\2)', css)


def condense_border_none(css):
    """Condense border:none; to border:0;."""
    log.debug("Condense borders values.")
    return css.replace("border:none;", "border:0;")


def add_encoding(css):
    """Add @charset 'UTF-8'; if missing."""
    log.debug("Adding encoding declaration if needed.")
    return "@charset utf-8;" + css if "@charset" not in css.lower() else css


def restore_needed_space(css):
    """Fix CSS for some specific cases where a white space is needed."""
    return css.replace("!important", " !important").replace(  # !important
        "@media(", "@media (").replace(  # media queries # jpeg > jpg
            "data:image/jpeg;base64,", "data:image/jpg;base64,").rstrip("\n;")


def unquote_selectors(css):
    """Fix CSS for some specific selectors where Quotes is not needed."""
    log.debug("Removing unnecessary Quotes on selectors of CSS classes.")
    return re.compile('([a-zA-Z]+)="([a-zA-Z0-9-_\.]+)"]').sub(r'\1=\2]', css)


def css_minify(css, wrap=False, comments=False, sort=False):
    """Minify CSS main function."""
    log.info("Compressing CSS...")
    css = remove_comments(css) if not comments else css
    css = sort_properties(css) if sort else css
    css = unquote_selectors(css)
    css = condense_whitespace(css)
    css = remove_url_quotes(css)
    css = condense_xtra_named_colors(css)
    css = condense_font_weight(css)
    css = remove_unnecessary_whitespace(css)
    css = condense_std_named_colors(css)
    css = remove_unnecessary_semicolons(css)
    css = condense_zero_units(css)
    css = condense_multidimensional_zeros(css)
    css = condense_floating_points(css)
    css = normalize_rgb_colors_to_hex(css)
    css = condense_hex_colors(css)
    css = condense_border_none(css)
    css = wrap_css_lines(css, 80) if wrap else css
    css = condense_semicolons(css)
    css = add_encoding(css)
    css = restore_needed_space(css)
    log.info("Finished compressing CSS !.")
    return css.strip()


##############################################################################
# HTML Minify


def condense_html_whitespace(html):
    """Condense HTML, but be safe first if it have textareas or pre tags.

    >>> condense_html_whitespace('<i>  <b>    <a> test </a>    </b> </i><br>')
    '<i><b><a> test </a></b></i><br>'
    """  # first space between tags, then empty new lines and in-between.
    log.debug("Removing unnecessary HTML White Spaces and Empty New Lines.")
    is_ok = "<textarea" not in html.lower() and "<pre" not in html.lower()
    html = re.sub(r'>\s+<', '> <', html) if is_ok else html
    return re.sub(r'\s{2,}|[\r\n]', ' ', html) if is_ok else html.strip()


def condense_style(html):
    """Condense style html tags.

    >>> condense_style('<style type="text/css">*{border:0}</style><p>a b c')
    '<style>*{border:0}</style><p>a b c'
    """  # May look silly but Emmet does this and is wrong.
    log.debug("Condensing HTML Style CSS tags.")
    return html.replace('<style type="text/css">', '<style>').replace(
        "<style type='text/css'>", '<style>').replace(
            "<style type=text/css>", '<style>')


def condense_script(html):
    """Condense script html tags.

    >>> condense_script('<script type="text/javascript"> </script><p>a b c')
    '<script> </script><p>a b c'
    """  # May look silly but Emmet does this and is wrong.
    log.debug("Condensing HTML Script JS tags.")
    return html.replace('<script type="text/javascript">', '<script>').replace(
        "<style type='text/javascript'>", '<script>').replace(
            "<style type=text/javascript>", '<script>')


def clean_unneeded_html_tags(html):
    """Clean unneeded optional html tags.

    >>> clean_unneeded_html_tags('a<body></img></td>b</th></tr></hr></br>c')
    'abc'
    """
    log.debug("Removing unnecessary optional HTML tags.")
    for tag_to_remove in ("""</area> </base> <body> </body> </br> </col>
        </colgroup> </dd> </dt> <head> </head> </hr> <html> </html> </img>
        </input> </li> </link> </meta> </option> </param> <tbody> </tbody>
        </td> </tfoot> </th> </thead> </tr> </basefont> </isindex> </param>
            """.split()):
            html = html.replace(tag_to_remove, '')
    return html  # May look silly but Emmet does this and is wrong.


def remove_html_comments(html):
    """Remove all HTML comments, Keep all for Grunt, Grymt and IE.

    >>> _="<!-- build:dev -->a<!-- endbuild -->b<!--[if IE 7]>c<![endif]--> "
    >>> _+= "<!-- kill me please -->keep" ; remove_html_comments(_)
    '<!-- build:dev -->a<!-- endbuild -->b<!--[if IE 7]>c<![endif]--> keep'
    """  # Grunt uses comments to as build arguments, bad practice but still.
    log.debug("""Removing all unnecessary HTML comments; Keep all containing:
    'build:', 'endbuild', '<!--[if]>', '<![endif]-->' for Grunt/Grymt, IE.""")
    return re.compile('<!-- [^(build|endbuild)].*? -->', re.I).sub('', html)


def unquote_html_attributes(html):
    """Remove all HTML quotes on attibutes if possible.

    >>> unquote_html_attributes('<img   width="9" height="5" data-foo="0"  >')
    '<img width=9 height=5 data-foo=0 >'
    """  # data-foo=0> might cause errors on IE, we leave 1 space data-foo=0 >
    log.debug("Removing unnecessary Quotes on attributes of HTML tags.")
    # cache all regular expressions on variables before we enter the for loop.
    any_tag = re.compile(r"<\w.*?>", re.I | re.MULTILINE | re.DOTALL)
    space = re.compile(r' \s+|\s +', re.MULTILINE)
    space1 = re.compile(r'\w\s+\w', re.MULTILINE)
    space2 = re.compile(r'"\s+>', re.MULTILINE)
    space3 = re.compile(r"'\s+>", re.MULTILINE)
    space4 = re.compile('"\s\s+\w+="|\'\s\s+\w+=\'|"\s\s+\w+=|\'\s\s+\w+=',
                        re.MULTILINE)
    space6 = re.compile(r"\d\s+>", re.MULTILINE)
    quotes_in_tag = re.compile('([a-zA-Z]+)="([a-zA-Z0-9-_\.]+)"')
    # iterate on a for loop cleaning stuff up on the html markup.
    for tag in iter(any_tag.findall(html)):
        # exceptions of comments and closing tags
        if tag.startswith('<!') or tag.find('</') > -1:
            continue
        original = tag
        # remove white space inside the tag itself
        tag = space2.sub('" >', tag)  # preserve 1 white space is safer
        tag = space3.sub("' >", tag)
        for each in space1.findall(tag) + space6.findall(tag):
            tag = tag.replace(each, space.sub(' ', each))
        for each in space4.findall(tag):
            tag = tag.replace(each, each[0] + ' ' + each[1:].lstrip())
        # remove quotes on some attributes
        tag = quotes_in_tag.sub(r'\1=\2 ', tag)  # See Bug #28
        if original != tag:  # has the tag been improved ?
            html = html.replace(original, tag)
    return html.strip()


def html_minify(html, comments=False):
    """Minify HTML main function.

    >>> html_minify(' <p  width="9" height="5"  > <!-- a --> b </p> c <br> ')
    '<p width=9 height=5 > b c <br>'
    """
    log.info("Compressing HTML...")
    html = remove_html_comments(html) if not comments else html
    html = condense_style(html)
    html = condense_script(html)
    html = clean_unneeded_html_tags(html)
    html = condense_html_whitespace(html)
    html = unquote_html_attributes(html)
    log.info("Finished compressing HTML !.")
    return html.strip()


##############################################################################
# JS Minify


def remove_commented_lines(js):
    """Force remove commented out lines from Javascript."""
    log.debug("Force remove commented out lines from Javascript.")
    result = ""
    for line in js.splitlines():
        if line.strip().startswith("//") and not line.strip().endswith("*/"):
            continue
        result += line
    return result


def simple_replacer_js(js):
    """Force strip simple replacements from Javascript."""
    log.debug("Force strip simple replacements from Javascript.")
    return condense_semicolons(js.replace("debugger;", ";").replace(
        ";}", "}").replace("; ", ";").replace(" ;", ";").rstrip("\n;"))


def js_minify_keep_comments(js):
    """Return a minified version of the Javascript string."""
    log.info("Compressing Javascript...")
    ins, outs = StringIO(js), StringIO()
    JavascriptMinify(ins, outs).minify()
    return force_single_line_js(outs.getvalue())


def force_single_line_js(js):
    """Force Javascript to a single line, even if need to add semicolon."""
    log.debug("Forcing JS from ~{0} to 1 Line.".format(len(js.splitlines())))
    return ";".join(js.splitlines()) if len(js.splitlines()) > 1 else js


class JavascriptMinify(object):

    """Minify an input stream of Javascript, writing to an output stream."""

    def __init__(self, instream=None, outstream=None):
        """Init class."""
        self.ins, self.outs = instream, outstream

    def minify(self, instream=None, outstream=None):
        """Minify Javascript using StringIO."""
        if instream and outstream:
            self.ins, self.outs = instream, outstream
        write, read = self.outs.write, self.ins.read
        space_strings = ("abcdefghijklmnopqrstuvwxyz"
                         "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$\\")
        starters, enders = '{[(+-', '}])+-"\''
        newlinestart_strings = starters + space_strings
        newlineend_strings = enders + space_strings
        do_newline, do_space = False, False
        doing_single_comment, doing_multi_comment = False, False
        previous_before_comment, in_quote = '', ''
        in_re, quote_buf = False, []
        previous = read(1)
        next1 = read(1)
        if previous == '/':
            if next1 == '/':
                doing_single_comment = True
            elif next1 == '*':
                doing_multi_comment = True
            else:
                write(previous)
        elif not previous:
            return
        elif previous >= '!':
            if previous in "'\"":
                in_quote = previous
            write(previous)
            previous_non_space = previous
        else:
            previous_non_space = ' '
        if not next1:
            return
        while True:
            next2 = read(1)
            if not next2:
                last = next1.strip()
                conditional_1 = (doing_single_comment or doing_multi_comment)
                if not conditional_1 and last not in ('', '/'):
                    write(last)
                break
            if doing_multi_comment:
                if next1 == '*' and next2 == '/':
                    doing_multi_comment = False
                    next2 = read(1)
            elif doing_single_comment:
                if next1 in '\r\n':
                    doing_single_comment = False
                    while next2 in '\r\n':
                        next2 = read(1)
                        if not next2:
                            break
                    if previous_before_comment in ')}]':
                        do_newline = True
                    elif previous_before_comment in space_strings:
                        write('\n')
            elif in_quote:
                quote_buf.append(next1)

                if next1 == in_quote:
                    numslashes = 0
                    for c in reversed(quote_buf[:-1]):
                        if c != '\\':
                            break
                        else:
                            numslashes += 1
                    if numslashes % 2 == 0:
                        in_quote = ''
                        write(''.join(quote_buf))
            elif next1 in '\r\n':
                conditional_2 = previous_non_space in newlineend_strings
                if conditional_2 or previous_non_space > '~':
                    while 1:
                        if next2 < '!':
                            next2 = read(1)
                            if not next2:
                                break
                        else:
                            conditional_3 = next2 in newlinestart_strings
                            if conditional_3 or next2 > '~' or next2 == '/':
                                do_newline = True
                            break
            elif next1 < '!' and not in_re:
                conditional_4 = next2 in space_strings or next2 > '~'
                conditional_5 = previous_non_space in space_strings
                conditional_6 = previous_non_space > '~'
                if (conditional_5 or conditional_6) and (conditional_4):
                    do_space = True
            elif next1 == '/':
                if in_re:
                    if previous != '\\':
                        in_re = False
                    write('/')
                elif next2 == '/':
                    doing_single_comment = True
                    previous_before_comment = previous_non_space
                elif next2 == '*':
                    doing_multi_comment = True
                else:
                    in_re = previous_non_space in '(,=:[?!&|'
                    write('/')
            else:
                if do_space:
                    do_space = False
                    write(' ')
                if do_newline:
                    write('\n')
                    do_newline = False
                write(next1)
                if not in_re and next1 in "'\"":
                    in_quote = next1
                    quote_buf = []
            previous = next1
            next1 = next2
            if previous >= '!':
                previous_non_space = previous


def js_minify(js):
    """Minify a JavaScript string."""
    js = remove_commented_lines(js)
    js = js_minify_keep_comments(js)
    return js.strip()


##############################################################################


def process_multiple_files(file_path, watch=False, wrap=False, timestamp=False,
        comments=False, sort=False, overwrite=False, gzip=False, prefix='',
        add_hash=False):
    """Process multiple CSS, JS, HTML files with multiprocessing."""
    log.debug("Process {} is Compressing {}.".format(os.getpid(), file_path))
    if watch:
        previous = int(os.stat(file_path).st_mtime)
        log.info("Process {} is Watching {}.".format(os.getpid(), file_path))
        while True:
            actual = int(os.stat(file_path).st_mtime)
            if previous == actual:
                sleep(60)
            else:
                previous = actual
                log.debug("Modification detected on {0}.".format(file_path))
                check_folder(os.path.dirname(file_path))
                if file_path.endswith(".css"):
                    process_single_css_file(file_path, wrap=wrap, timestamp=timestamp,
                        comments=comments, sort=sort, overwrite=overwrite, gzip=gzip,
                        prefix=prefix, add_hash=add_hash)
                elif file_path.endswith(".js"):
                    process_single_js_file(file_path, timestamp=timestamp,
                        overwrite=overwrite, gzip=gzip)
                else:
                    process_single_html_file(file_path, comments=comments,
                        overwrite=overwrite, prefix=prefix, add_hash=add_hash)
    else:
        if file_path.endswith(".css"):
            process_single_css_file(file_path, wrap=wrap, timestamp=timestamp,
                comments=comments, sort=sort, overwrite=overwrite, gzip=gzip,
                prefix=prefix, add_hash=add_hash)
        elif file_path.endswith(".js"):
            process_single_js_file(file_path, timestamp=timestamp,
                overwrite=overwrite, gzip=gzip)
        else:
            process_single_html_file(file_path, comments=comments,
                overwrite=overwrite, prefix=prefix, add_hash=add_hash)


def prefixer_extensioner(file_path, old, new, file_content=None, prefix='', add_hash=False):
    """Take a file path and safely preppend a prefix and change extension.

    This is needed because filepath.replace('.foo', '.bar') sometimes may
    replace '/folder.foo/file.foo' into '/folder.bar/file.bar' wrong!.
    >>> prefixer_extensioner('/tmp/test.js', '.js', '.min.js')
    '/tmp/test.min.js'
    """
    log.debug("Prepending '{}' Prefix to {}.".format(new.upper(), file_path))
    extension = os.path.splitext(file_path)[1].lower().replace(old, new)
    filenames = os.path.splitext(os.path.basename(file_path))[0]
    filenames = prefix + filenames if prefix else filenames
    if add_hash and file_content:  # http://stackoverflow.com/a/25568916
        filenames += "-" + sha1(file_content.encode("utf-8")).hexdigest()[:11]
        log.debug("Appending SHA1 HEX-Digest Hash to '{}'.".format(file_path))
    dir_names = os.path.dirname(file_path)
    file_path = os.path.join(dir_names, filenames + extension)
    return file_path


def process_single_css_file(css_file_path, wrap=False, timestamp=False,
        comments=False, sort=False, overwrite=False, gzip=False,
        prefix='', add_hash=False, output_path=None):
    """Process a single CSS file."""
    log.info("Processing CSS file: {0}.".format(css_file_path))
    with open(css_file_path, encoding="utf-8") as css_file:
        original_css = css_file.read()
    log.debug("INPUT: Reading CSS file {}.".format(css_file_path))
    minified_css = css_minify(original_css, wrap=wrap,
                              comments=comments, sort=sort)
    if timestamp:
        taim = "/* {0} */ ".format(datetime.now().isoformat()[:-7].lower())
        minified_css = taim + minified_css
    if output_path is None:
        min_css_file_path = prefixer_extensioner(
            css_file_path, ".css", ".css" if overwrite else ".min.css",
            original_css, prefix=prefix, add_hash=add_hash)
        if gzip:
            gz_file_path = prefixer_extensioner(
                css_file_path, ".css",
                ".css.gz" if overwrite else ".min.css.gz", original_css,
                prefix=prefix, add_hash=add_hash)
            log.debug("OUTPUT: Writing gZIP CSS Minified {}.".format(gz_file_path))
    else:
        min_css_file_path = gz_file_path = output_path
    if not gzip or output_path is None:
        # if a specific output path is requested, then write write only one output file
        with open(min_css_file_path, "w", encoding="utf-8") as output_file:
            output_file.write(minified_css)
    if gzip:
        with gzip.open(gz_file_path, "wt", encoding="utf-8") as output_gz:
            output_gz.write(minified_css)
    log.debug("OUTPUT: Writing CSS Minified {0}.".format(min_css_file_path))
    return min_css_file_path


def process_single_html_file(html_file_path, comments=False,
        overwrite=False, prefix='', add_hash=False, output_path=None):
    """Process a single HTML file."""
    log.info("Processing HTML file: {0}.".format(html_file_path))
    with open(html_file_path, encoding="utf-8") as html_file:
        minified_html = html_minify(html_file.read(),
        comments=comments)
    log.debug("INPUT: Reading HTML file {0}.".format(html_file_path))
    if output_path is None:
        html_file_path = prefixer_extensioner(
            html_file_path, ".html" if overwrite else ".htm", ".html",
            prefix=prefix, add_hash=add_hash)
    else:
        html_file_path = output_path
    with open(html_file_path, "w", encoding="utf-8") as output_file:
        output_file.write(minified_html)
    log.debug("OUTPUT: Writing HTML Minified {0}.".format(html_file_path))
    return html_file_path


def process_single_js_file(js_file_path, timestamp=False,
        overwrite=False, gzip=False, output_path=None):
    """Process a single JS file."""
    log.info("Processing JS file: {0}.".format(js_file_path))
    with open(js_file_path, encoding="utf-8") as js_file:
        original_js = js_file.read()
    log.debug("INPUT: Reading JS file {0}.".format(js_file_path))
    minified_js = js_minify(original_js)
    if timestamp:
        taim = "/* {} */ ".format(datetime.now().isoformat()[:-7].lower())
        minified_js = taim + minified_js
    if output_path is None:
        min_js_file_path = prefixer_extensioner(
            js_file_path, ".js", ".js" if overwrite else ".min.js",
            original_js)
        if gzip:
            gz_file_path = prefixer_extensioner(
                js_file_path, ".js", ".js.gz" if overwrite else ".min.js.gz",
                original_js)
            log.debug("OUTPUT: Writing gZIP JS Minified {}.".format(gz_file_path))
    else:
        min_js_file_path = gz_file_path = output_path
    if not gzip or output_path is None:
        # if a specific output path is requested, then write write only one output file
        with open(min_js_file_path, "w", encoding="utf-8") as output_file:
           output_file.write(minified_js)
    if gzip:
        with gzip.open(gz_file_path, "wt", encoding="utf-8") as output_gz:
            output_gz.write(minified_js)
    log.debug("OUTPUT: Writing JS Minified {0}.".format(min_js_file_path))
    return min_js_file_path


def make_arguments_parser():
    """Build and return a command line agument parser."""
    parser = ArgumentParser(description=__doc__, epilog="""CSS-HTML-JS-Minify:
    Takes a file or folder full path string and process all CSS/HTML/JS found.
    If argument is not file/folder will fail. Check Updates works on Python3.
    Std-In to Std-Out is deprecated since it may fail with unicode characters.
    SHA1 HEX-Digest 11 Chars Hash on Filenames is used for Server Cache.
    CSS Properties are Alpha-Sorted, to help spot cloned ones, Selectors not.
    Watch works for whole folders, with minimum of ~60 Secs between runs.""")
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('fullpath', metavar='fullpath', type=str,
                        help='Full path to local file or folder.')
    parser.add_argument('--wrap', action='store_true',
                        help="Wrap output to ~80 chars per line, CSS only.")
    parser.add_argument('--prefix', type=str,
                        help="Prefix string to prepend on output filenames.")
    parser.add_argument('--timestamp', action='store_true',
                        help="Add a Time Stamp on all CSS/JS output files.")
    parser.add_argument('--quiet', action='store_true', help="Quiet, Silent.")
    parser.add_argument('--hash', action='store_true',
                        help="Add SHA1 HEX-Digest 11chars Hash to Filenames.")
    parser.add_argument('--gzip', action='store_true',
                        help="GZIP Minified files as '*.gz', CSS/JS only.")
    parser.add_argument('--sort', action='store_true',
                        help="Alphabetically Sort CSS Properties, CSS only.")
    parser.add_argument('--comments', action='store_true',
                        help="Keep comments, CSS/HTML only (Not Recommended)")
    parser.add_argument('--overwrite', action='store_true',
                        help="Force overwrite all in-place (Not Recommended)")
    parser.add_argument('--after', type=str,
                        help="Command to execute after run (Experimental).")
    parser.add_argument('--before', type=str,
                        help="Command to execute before run (Experimental).")
    parser.add_argument('--watch', action='store_true', help="Watch changes.")
    parser.add_argument('--multiple', action='store_true',
                        help="Allow Multiple instances (Not Recommended).")
    # global args
    return parser.parse_args()


def prepare():
    make_logger("css-html-js-minify")  # AutoMagically make a Logger Log
    check_encoding()  # AutoMagically Check Encodings/root
    set_process_name("css-html-js-minify")  # set Name
    set_single_instance("css-html-js-minify")  # AutoMagically set Single Instance


def main():
    """Main Loop."""
    args = make_arguments_parser()
    log.disable(log.CRITICAL) if args.quiet else log.debug("Max Logging ON")
    log.info(__doc__ + __version__)
    check_folder(os.path.dirname(args.fullpath))
    if os.path.isfile(args.fullpath) and args.fullpath.endswith(".css"):
        log.info("Target is a CSS File.")  # Work based on if argument is
        list_of_files = str(args.fullpath)  # file or folder, folder is slower.
        process_single_css_file(args.fullpath, wrap=args.wrap, timestamp=args.timestamp,
            comments=args.comments, sort=args.sort, overwrite=args.overwrite, gzip=args.gzip,
            prefix=args.prefix, add_hash=args.hash)
    elif os.path.isfile(args.fullpath) and args.fullpath.endswith(
            ".html" if args.overwrite else ".htm"):
        log.info("Target is HTML File.")
        list_of_files = str(args.fullpath)
        process_single_html_file(args.fullpath, comments=args.comments,
            overwrite=args.overwrite, prefix=args.prefix, add_hash=args.hash)
    elif os.path.isfile(args.fullpath) and args.fullpath.endswith(".js"):
        log.info("Target is a JS File.")
        list_of_files = str(args.fullpath)
        process_single_js_file(args.fullpath, timestamp=args.timestamp,
            overwrite=args.overwrite, gzip=args.gzip)
    elif os.path.isdir(args.fullpath):
        log.info("Target is a Folder with CSS, HTML, JS files !.")
        log.warning("Processing a whole Folder may take some time...")
        list_of_files = walk2list(
            args.fullpath,
            (".css", ".js", ".html" if args.overwrite else ".htm"),
            (".min.css", ".min.js", ".htm" if args.overwrite else ".html"))
        log.info('Total Maximum CPUs used: ~{0} Cores.'.format(cpu_count()))
        pool = Pool(cpu_count())  # Multiprocessing Async
        pool.map_async(partial(process_multiple_files, watch=args.watch,
                wrap=args.wrap, timestamp=args.timestamp, comments=args.comments,
                sort=args.sort, overwrite=args.overwrite, gzip=args.gzip,
                prefix=args.prefix, add_hash=args.hash),
            list_of_files)
        pool.close()
        pool.join()
    else:
        log.critical("File or folder not found,or cant be read,or I/O Error.")
        sys.exit(1)
    if args.after and getoutput:
        log.info(getoutput(str(args.after)))
    log.info('\n {0} \n Files Processed: {1}.'.format('-' * 80, list_of_files))
    log.info('Number of Files Processed: {0}.'.format(
        len(list_of_files) if isinstance(list_of_files, tuple) else 1))
    make_post_exec_msg(start_time)
