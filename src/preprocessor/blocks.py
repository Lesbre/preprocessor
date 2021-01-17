# -*- coding: utf-8 -*-
import argparse
import re
from typing import Callable, Iterable, List, Optional, Tuple

from .conditions import condition_eval, find_matching_close_parenthese
from .defs import (REGEX_IDENTIFIER, REGEX_IDENTIFIER_END, REGEX_INTEGER,
                   ArgumentParserNoExit, TokenMatch, to_integer)
from .preprocessor import Preprocessor

# ============================================================
# simple blocks (void, block, verbatim)
# ============================================================

def blck_void(p: Preprocessor, args: str, contents: str) -> str:
	"""The void block, processes commands inside it but prints nothing"""
	if args.strip() != "":
		p.send_warning("the void block takes no arguments")
	p.context.update(p.current_position.end, "in void block")
	contents = p.parse(contents)
	p.context.pop()
	return ""

blck_void.doc = ( # type: ignore
	"""
	This block is pared but not printed.
	Use it to place comments or a bunch of def
	without adding whitespace""")

def blck_block(p: Preprocessor, args: str, contents: str) -> str:
	"""The block block. It does nothing but ensure post action
	declared in this block don't affect the rest of the file"""
	if args.strip() != "":
		p.send_warning("the block block takes no arguments")
	p.context.update(p.current_position.end, "in block block")
	contents = p.parse(contents)
	p.context.pop()
	return contents

blck_block.doc = ( # type: ignore
	"""
	Simple block used to restrict scope of final action commands
	""")

def blck_verbatim(p: Preprocessor, args: str, contents: str) -> str:
	"""The verbatim block. It copies its content without parsing them
	Stops at first {% endverbatim %} not matching a {% verbatim %}"""
	if args.strip() != "":
		p.send_warning("the verbatim block takes no arguments")
	return contents

blck_verbatim.doc = ( # type: ignore
	"""
	Used to paste contents without parsing them
	Stops at first {% endverbatim %} not matching a {% verbatim %}.

	Ex:
	  "{% verbatim %}some text with symbols {% and %}{% endverbatim %}"
	Prints:
	  "some text with symbols {% and %}"

	Ex:
	  "{% verbatim %}some text with {% verbatim %}nested verbatim{% endverbatim %}{% endverbatim %}"
	Prints:
	  "some text with {% verbatim %}nested verbatim{% endverbatim %}"
	""")

def blck_repeat(p: Preprocessor, args: str, contents: str) -> str:
	"""The repeat block.
	usage: repeat <number>
		renders its contents one and copies them number times"""
	args = args.strip()
	if not args.isnumeric():
		p.send_error("invalid argument. Usage: repeat [uint > 0]")
	nb = int(args)
	if nb <= 0:
		p.send_error("invalid argument. Usage: repeat [uint > 0]")
	p.context.update(p.current_position.end, "in block repeat")
	contents = p.parse(contents)
	p.context.pop()
	return contents * nb

blck_repeat.doc = ( # type: ignore
	"""
	Used to repeat a block of text a number of times

	Usage: repeat <number>

	Ex: "{% repeat 4 %}a{% endrepeat %}" prints "aaaa".

	Unlike {% for x in range(3) %}, {% repeat 3 %} only
	  renders the block once and prints three copies.
	""")


# ============================================================
# atlabel block
# ============================================================


def blck_atlabel(p: Preprocessor, args: str, contents: str) -> str:
	"""the atlabel block
	usage: atlabel <label>
	renders its contents and stores them
	add a post action to place itself at all labels <label>"""
	lbl = args.strip()
	if lbl == "":
		p.send_error("empty label name")
	if "atlabel" in p.command_vars:
		if lbl in p.command_vars["atlabel"]:
			p.send_error('Multiple atlabel blocks with same label "{}"'.format(lbl))
	else:
		p.command_vars["atlabel"] = dict()

	p.context.update(p.current_position.end, "in block atlabel")
	p.command_vars["atlabel"][lbl] = p.parse(contents)
	p.context.pop()
	return ""

blck_atlabel.doc = ( # type: ignore
	"""
	Renders a chunk of text and places it at all labels matching
	its label when processing is done.

	Usage: atlabel <label>

	It differs from the cut block in that:
	- it will also print its content to calls of {% label XXX %} preceding it
	- it canno't be overwritting (at most one atlabel block per label)
	- the text is rendered in the block (and not in where the text is pared)

	ex:
	  "{% def foo bar %}
	  first label: {% label my_label %}
	  {% atlabel my_label %}foo is {% foo %}{% endatlabel %}
		{% def foo notbar %}
	  second label: {% label my_label %}"
	prints:
	  "
	  first label: foo is bar

	  second label: foo is bar"
	""")

def fnl_atlabel(pre: Preprocessor, string: str) -> str:
	"""places atlabel blocks at all matching labels"""
	if "atlabel" in pre.command_vars:
		deletions = []
		for lbl in pre.command_vars["atlabel"]:
			nb_labels = len(pre.labels.get_label(lbl))
			if nb_labels == 0:
				pre.send_warning('No matching label for atlabel block "{}"'.format(lbl))
			for i in range(nb_labels):
				index = pre.labels.get_label(lbl)[i]
				string = pre.replace_string(
					index, index, string, pre.command_vars["atlabel"][lbl], []
				)
			deletions.append(lbl)
		for lbl in deletions:
			del pre.command_vars["atlabel"][lbl]
	return string


# ============================================================
# for block
# ============================================================


def blck_for(pre: Preprocessor, args: str, contents: str) -> str:
	"""The for block, simple for loop
	usage: for <ident> in range(stop)
	                      range(start, stop)
	                      range(start, stop, step)
	       for <ident> in space separated list " argument with spaces"
	"""
	match = re.match(r"^\s*({})\s+in\s+".format(REGEX_IDENTIFIER), args)
	if match is None:
		pre.send_error(
			"Invalid syntax.\n"
			"usage: for <ident> in range(stop)\n"
	    "                      range(start, stop)\n"
			"                      range(start, stop, step)\n"
			"       for <ident> in space separated list \" argument with spaces\""
		)
		return ""
	ident = match.group(1)
	args = args[match.end():].strip()
	iterator: Iterable = []
	if args[0:5] == "range":
		regex = r"range\((?:\s*({nb})\s*,)?\s*({nb})\s*(?:,\s*({nb})\s*)?\)".format(
			nb = REGEX_INTEGER)
		match = re.match(regex, args)
		if match is None:
			pre.send_error(
				"Invalid range syntax in for.\n"
				"usage: range(stop) or range(start, stop) or range(start, stop, step)\n"
				"  start, stop and step, should be integers (contain only 0-9 or _, with an optional leading -)"
			)
			return ""
		groups = match.groups()
		start = 0
		step = 1
		stop = to_integer(groups[1])
		if groups[0] is not None:
			start = to_integer(groups[0])
			if groups[2] is not None:
				step = to_integer(groups[2])
		iterator = range(start, stop, step)
	else:
		iterator = pre.split_args(args)
	result = ""
	for value in iterator:
		def defined_value(pr: Preprocessor, args: str) -> str:
			"""new command defined in for block"""
			if args.strip() != "":
				pr.send_warning(
					"Extra arguments.\nThe command {} defined in for loop takes no arguments".format(ident)
				)
			return str(value)
		defined_value.__name__ = "for_cmd_{}".format(ident)
		defined_value.__doc__ = "Command defined in for loop: {} = '{}'".format(ident, value)
		defined_value.doc = defined_value.__doc__ # type: ignore
		pre.commands[ident] = defined_value
		pre.context.update(pre.current_position.end, "in for block")
		result += pre.parse(contents)
		pre.context.pop()
	return result

blck_for.doc = ( # type: ignore
	"""
	Simple for loop used to render a chunk of text multiple times.
	ex: "{% for x in range(2) %}{% x %},{% endfor %}" -> "1,2,"

	Usage: for <ident> in range(stop)
	                      range(start, stop)
	                      range(start, stop, step)
	       for <ident> in space separated list " argument with spaces"


	range can be combined with the deflist command to iterate multiple lists:

	  "{% deflist names alice john frank %} {% deflist ages 23 31 19 %}
	  {% for i in range(3) %}{% names {% i %} %} (age {% ages {% i %} %})
	  {% endfor %}"

	prints:

	  "
	  alice (age 23)
	  john (age 31)
	  frank (age 19)
	  "

	""")


# ============================================================
# cut block
# ============================================================


cut_parser = ArgumentParserNoExit(prog="cut", add_help=False)
cut_parser.add_argument("--pre-render", "-p", action="store_true")
cut_parser.add_argument("clipboard", nargs="?", default="")

def blck_cut(pre: Preprocessor, args: str, contents: str) -> str:
	"""the cut block.
	usage: cut [--pre-render|-p] [<clipboard_name>]
		if --pre-render - renders the block here
		  (will be rerendered at time of pasting, unless using paste -v|--verbatim)
		clipboard is a string identifying the clipboard, default is ""
	"""
	split = pre.split_args(args)
	try:
		arguments = cut_parser.parse_args(split)
	except argparse.ArgumentError:
		pre.send_error("invalid argument.\nusage: cut [--pre-render|-p] [<clipboard_name>]")
	clipboard = arguments.clipboard
	pos = pre.current_position.end
	context = pre.context.top.copy(pos, "in pasted block")
	if arguments.pre_render:
		pre.context.update(pos, "in cut block")
		contents = pre.parse(contents)
		pre.context.pop()
	if "clipboard" not in pre.command_vars:
		pre.command_vars["clipboard"] = {clipboard: (context, contents)}
	else:
		pre.command_vars["clipboard"][clipboard] = (context, contents)
	return ""

blck_cut.doc = ( # type: ignore
	"""
	Used to cut a section of text to paste elsewhere.
	The text is processed when pasted, not when cut

	Usage: cut [--pre-render|-p] [<clipboard_name>]
	  if --pre-render - renders the block here
	    (will be rerendered at time of pasting, unless using paste -v|--verbatim)
	  clipboard is a string identifying the clipboard, default is ""

	ex:
	  {% cut %}foo is {% foo %}{% endcut %}
	  {% def foo bar %}
	  first paste: {% paste %}
	  {% def foo notbar %}
	  second paste: {% paste %}"
	prints:
	  "

	  first paste: foo is bar

	  second paste: foo is notbar"
	""")


# ============================================================
# if block
# ============================================================

def find_elifs_and_else(preproc: Preprocessor, string: str
	) -> Tuple[int, int, Optional[str]]:
	"""returns a tuple indicating the next elif/else:
	(-1,-1,None) -> no matching elif/else
	(begin, end, None) -> matching else at string[begin:end]
	(begin, end, str) -> matchin elif with arguments str at string[begin:end]"""
	tokens = preproc._find_tokens(string)
	depth = 0
	endif_regex = r"\s*{}if\s*{}".format(
		re.escape(preproc.token_endblock),
		re.escape(preproc.token_end)
	)
	if_regex = r"\s*if(?:{}|{})".format(
		re.escape(preproc.token_end), REGEX_IDENTIFIER_END
	)
	elif_regex = r"\s*(elif)(?:{}|{})".format(
		re.escape(preproc.token_end), REGEX_IDENTIFIER_END
	)
	else_regex = r"\s*else\s*{}".format(re.escape(preproc.token_end))
	for i, (begin, end, token) in enumerate(tokens):
		if token == TokenMatch.OPEN:
			if re.match(if_regex, string[end:], preproc.re_flags) is not None:
				depth += 1
			elif re.match(endif_regex, string[end:], preproc.re_flags) is not None:
				depth -= 1
			elif depth == 0:
				match_else = re.match(else_regex, string[end:], preproc.re_flags)
				match_elif = re.match(elif_regex, string[end:], preproc.re_flags)
				if match_else is not None:
					return (begin, end + match_else.end(), None)
				if match_elif is not None:
					parenthese = ["(" if x[2] == TokenMatch.OPEN else ")" for x in tokens]
					j = find_matching_close_parenthese(parenthese, i)
					if j == len(tokens):
						preproc.context.update(begin + preproc.current_position.end, "in elif")
						preproc.send_error(
							'Unmatched "{}" token in endif.\n'
							'Add matching "{}" or use "{}begin{}" to place it.'.format(
							preproc.token_begin, preproc.token_end, preproc.token_begin, preproc.token_end
						))
						preproc.context.pop()
					end += match_elif.end(1)
					return (begin, tokens[j][1], string[end:tokens[j][0]])
	return (-1, -1, None)

def blck_if(preprocessor: Preprocessor, args: str, contents: str) -> str:
	"""the if block
	usage: {% if <condition> %} ...
	       [{% elif <condition> %} ...]
	       [{% else %}...]
	       {% endif %}
	"""
	value = condition_eval(preprocessor, args)
	pos_0 = 0
	desc = "in if block"
	while True:
		else_info = find_elifs_and_else(preprocessor, contents[pos_0:])
		if value:
			endelse = pos_0 + else_info[0] if else_info[0] != -1 else len(contents)
			preprocessor.context.update(pos_0 + preprocessor.current_position.end, desc)
			parsed = preprocessor.parse(contents[pos_0:endelse])
			preprocessor.context.pop()
			return parsed
		if else_info[0] == -1:
			# no matching else
			return ""
		if else_info[2] is None:
			value = not value
			desc = "in else"
		else:
			preprocessor.context.update(
				pos_0 + else_info[0] + preprocessor.current_position.end,
				"in elif evaluation"
			)
			args = preprocessor.parse(else_info[2])
			preprocessor.context.pop()
			value = condition_eval(preprocessor, args)
			desc = "in elif"
		pos_0 += else_info[1]

blck_if.doc = ( # type: ignore
	"""
	Used to select wether or not to render a chunk of text
	based on simple conditions
	ex :
	  {% if def identifier %}, {% if ndef identifier %}...
	  {% if {% var %}==str_value %}, {% if {% var %}!=str_value %}...

	Usage: {% if <condition> %} ...
	       [{% elif <condition> %} ...]
	       [{% else %}...]
	       {% endif %}

	Condition syntax is as follows
	  simple_condition =
	    | true | false | 1 | 0 | <string>
	    | def <identifier> | ndef <identifier>
	    | <str> == <str> | <str> != <str>

	  condition =
	    | <simple_condition> | not <simple_condition>
	    | <condition> and <condition>
	    | <condition> or <condition>
	    | (<condition>)
	""")
