#!/usr/bin/env python
# -*- coding: UTF-8 -*-

"""
This file augments the AST generated by bashlex with single-command structure.
It also performs some normalization on the command arguments.
"""

from __future__ import print_function
import copy
import os
import re
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "grammar"))

# bashlex stuff
import bast, errors, tokenizer, bparser
import bash
from lookup import ManPageLookUp
from normalizer_node import *

_H_NO_EXPAND = b"<H_NO_EXPAND>"
_V_NO_EXPAND = b"<V_NO_EXPAND>"

binary_logic_operators = set([
    '-and',
    '-or',
    '||',
    '&&',
    '-o',
    '-a'
])

man_lookup = ManPageLookUp([os.path.join(os.path.dirname(__file__), "..", "grammar",
                                         "primitive_cmds_grammar.json")])

def type_check(node, possible_types):
    word = node.word
    """Heuristically determine argument types."""
    if word in ["+", ";", "{}"]:
        return "ReservedWord"
    if word.isdigit() and "Number" in possible_types:
        return "Number"
    if any(c.isdigit() for c in word):
        if word[-1] in ["k", "M", "G", "T", "P"] and "Size" in possible_types:
            return "Size"
        if word[-1] in ["s", "m", "h", "d", "w"] and "Time" in possible_types:
            return "Time"
    if "Permission" in possible_types:
        if any(c.isdigit() for c in word) or '=' in word:
            return "Permission"
    if "Pattern" in possible_types:
        if len(word) == node.pos[1] - node.pos[0] - 2:
            return "Pattern"
    if "File" in possible_types:
        return "File"
    elif "Utility" in possible_types:
        # TODO: this argument type is not well-handled
        # This is usuallly third-party utitlies
        return "Utility"
    else:
        print("Warning: unable to decide type for {}, return \"Unknown\"."
              .format(word))
        return "Unknown"

def is_unary_logic_op(node, parent):
    if node.word == "!":
        return parent and parent.kind == "headcommand" \
               and parent.value == "find"
    return node.word in right_associate_unary_logic_operators \
           or node.word in left_associate_unary_logic_operators

def is_binary_logic_op(node, parent):
    if node.word == '-o':
        if parent and parent.kind == "headcommand" \
                and parent.value == "find":
            node.word = "-or"
            return True
        else:
            return False
    if node.word == '-a':
        if parent and parent.kind == "headcommand" \
                and parent.value == "find":
            node.word = "-and"
            return True
        else:
            return False
    return node.word in binary_logic_operators

def special_command_normalization(cmd):
    # special normalization for certain commands
    ## remove all "sudo"'s
    cmd = cmd.replace("sudo", "")

    ## normalize utilities called with full path
    cmd = cmd.replace("/usr/bin/find ", "find ")
    cmd = cmd.replace("/bin/find ", "find ")
    cmd = cmd.replace("/usr/bin/grep ", "grep ")
    cmd = cmd.replace("/bin/rm ", "rm ")
    cmd = cmd.replace("/bin/mv ", "mv ")
    cmd = cmd.replace("/bin/echo ", "echo ")
    cmd = cmd.replace("-i{}", "-I {}")
    cmd = cmd.replace("-I{}", "-I {}")
    cmd = cmd.replace("— ", "-")
    cmd = cmd.replace("—", "-")
    cmd = cmd.replace("-\xd0\xbe", "-o")
    cmd = cmd.replace(" [] ", " {} ")

    ## remove shell character
    if cmd.startswith("\$ "):
        cmd = re.sub("^\$ ", '', cmd)
    if cmd.startswith("\# "):
        cmd = re.sub("^\# ", '', cmd)
    if cmd.startswith("\$find "):
        cmd = re.sub("^\$find ", "find ", cmd)
    if cmd.startswith("\#find "):
        cmd = re.sub("^\#find ", "find ", cmd)

    ## correct common spelling errors
    cmd = cmd.replace("-\\(", "\\(")
    cmd = cmd.replace("-\\)", "\\)")
    cmd = cmd.replace("\"\\)", " \\)")
    cmd = cmd.replace('‘', '\'')
    cmd = cmd.replace('’', '\'')

    ## the first argument of "tar" is always interpreted as an option
    tar_fix = re.compile(' tar \w')
    if cmd.startswith('tar'):
        cmd = ' ' + cmd
        for w in re.findall(tar_fix, cmd):
            cmd = cmd.replace(w, w.replace('tar ', 'tar -'))
        cmd = cmd.strip()
    return cmd

def attach_to_tree(node, parent):
    node.parent = parent
    node.lsb = parent.getRightChild()
    parent.addChild(node)
    if node.lsb:
        node.lsb.rsb = node

def detach_from_tree(node, parent):
    if not parent:
        return
    parent.removeChild(node)
    parent = None
    if node.lsb:
        node.lsb.rsb = node.rsb
    if node.rsb:
        node.rsb.lsb = node.lsb
    node.rsb = None
    node.lsb = None

def normalize_ast(cmd, normalize_digits=True, normalize_long_pattern=True,
                  recover_quotation=True, verbose=False):
    """
    Convert the bashlex parse tree of a command into the normalized form.
    :param cmd: bash command to parse
    :param normalize_digits: replace all digits in the tree with the special
                             _NUM symbol
    :param recover_quotation: if set, retain quotation marks in the command
    :param verbose: if set, print error message.
    :return normalized_tree
    """
    cmd = cmd.replace('\n', ' ').strip()
    cmd = special_command_normalization(cmd)

    if not cmd:
        return None

    def normalize_word(node, kind, norm_digit, norm_long_pattern,
                       recover_quote, arg_type=""):
        w = recover_quotation(node) if recover_quote else node.word
        if kind == "argument" and arg_type != "Permission":
            if ' ' in w:
                try:
                    assert(w.startswith('"') and w.endswith('"'))
                except AssertionError, e:
                    if verbose:
                        print("Quotation Error: space inside word " + w)
                if norm_long_pattern:
                    w = bash._LONG_PATTERN
            if norm_digit:
                w = re.sub(bash._DIGIT_RE, bash._NUM, w)
        return w

    def recover_quotation(node):
        if with_quotation(node):
            return cmd[node.pos[0] : node.pos[1]]
        else:
            return node.word

    def with_quotation(node):
        return cmd[node.pos[0]] in ['"', '\''] \
               or cmd[node.pos[1]-1] in ['"', '\'']

    def normalize_argument(node, current, arg_type):
        value = normalize_word(node, "argument", normalize_digits,
                normalize_long_pattern, recover_quotation, arg_type=arg_type)
        norm_node = ArgumentNode(value=value, arg_type=arg_type)
        attach_to_tree(norm_node, current)
        return norm_node

    def normalize_flag(node, current):
        value = normalize_word(node, "flag", normalize_digits,
                               normalize_long_pattern, recover_quotation)
        norm_node = FlagNode(value=value)
        attach_to_tree(norm_node, current)
        return norm_node

    def normalize_headcommand(node, current):
        value = normalize_word(node, "headcommand", normalize_digits,
                               normalize_long_pattern, recover_quotation)
        norm_node = HeadCommandNode(value=value)
        attach_to_tree(norm_node, current)
        return norm_node

    def normalize_command(node, current):
        arg_status = None                       # determine argument types
        head_commands = []
        unary_logic_ops = []
        binary_logic_ops = []
        unprocessed_unary_logic_ops = []
        unprocessed_binary_logic_ops = []

        def expecting(a_t):
            for arg_type, is_list, filled in arg_status["non-optional"]:
                if not is_list and filled:
                    continue
                if arg_type == a_t:
                    return True
            for arg_type, is_list, filled in arg_status["optional"]:
                if not is_list and filled:
                    continue
                if arg_type == a_t:
                    return True
            return False
                    
        def cmd_arg_type_check(node):
            arg_types = {}
            for i in xrange(len(arg_status["non-optional"])):
                arg_type, is_list, filled = arg_status["non-optional"][i]
                if not is_list and filled:
                    continue
                arg_types[arg_type] = None
            for i in xrange(len(arg_status["non-optional"])):
                arg_type, is_list, filled = arg_status["non-optional"][i]
                if not is_list and filled:
                    continue
                arg_types[arg_type] = None

            assert(len(arg_types) > 0)
            arg_type = type_check(node, arg_types)

            for i in xrange(len(arg_status["non-optional"])):
                if arg_status["non-optional"][i][0] == arg_type:
                    arg_status["non-optional"][i][2] = True
            for i in xrange(len(arg_status["optional"])):
                if arg_status["non-optional"][i][0] == arg_type:
                    arg_status["non-optional"][i][2] = True

            return arg_type

        def organize_buffer(lparenth, rparenth):
            node = lparenth.rsb
            while node != rparenth:
                if node.kind == "unarylogicop":
                    adjust_unary_operators(node)
                node = node.rsb
            node = lparenth.rsb
            while node != rparenth:
                if node.kind == "binarylogicop":
                    adjust_binary_operators(node)
                node = node.rsb
            node = lparenth.rsb
            if node.rsb == rparenth:
                return lparenth.rsb
            else:
                norm_node = BinaryLogicOpNode(value="-and")
                while node != rparenth:
                    attach_to_tree(node, norm_node)
                    node = node.rsb
                return norm_node

        def adjust_unary_operators(node):
            if node.associate == UnaryLogicOpNode.RIGHT:
                # change right sibling to child
                rsb = node.rsb
                if not rsb:
                    print("Warning: unary logic operator without a right "
                          "sibling.")
                    print(node.parent)
                    return
                if rsb.value == "(":
                    unprocessed_unary_logic_ops.append(node)
                    return
                if rsb.value == ")":
                    # TODO: this corner case is not handled very well
                    node.associate = UnaryLogicOpNode.LEFT
                    unprocessed_unary_logic_ops.append(node)
                    return
                make_sibling(node, rsb.rsb)
                node.parent.removeChild(rsb)
                rsb.lsb = None
                rsb.rsb = None
                node.addChild(rsb)
            elif node.associate == UnaryLogicOpNode.LEFT:
                # change left sibling to child
                lsb = node.lsb
                if not lsb:
                    print("Warning: unary logic operator without a left "
                          "sibling.")
                    print(node.parent)
                    return
                if lsb.value == ")":
                    unprocessed_unary_logic_ops.append(node)
                    return
                if lsb.kind == "binarylogicop" or lsb.value == "(":
                    # TODO: this corner case is not handled very well
                    # it is often triggered by the bizarreness of -prune
                    return
                make_sibling(lsb.lsb, node)
                node.parent.removeChild(lsb)
                lsb.lsb = None
                lsb.rsb = None
                node.addChild(lsb)
            else:
                raise AttributeError("Cannot decide unary operator "
                                     "assocation: {}".format(node.symbok))

            # resolve single child of binary operators left as the result of
            # parentheses processing
            if node.parent.kind == "binarylogicop" \
                    and node.parent.value == "-and":
                if node.parent.getNumChildren() == 1:
                    node.grandparent().replaceChild(node.parent, node)

        def adjust_binary_operators(node):
            # change right sibling to Child
            # change left sibling to child
            rsb = node.rsb
            lsb = node.lsb

            if not rsb or not lsb:
                raise AttributeError("Error: binary logic operator must have "
                                     "both left and right siblings.")

            if rsb.value == "(" or lsb.value == ")":
                unprocessed_binary_logic_ops.append(node)
                # sibling is parenthese
                return

            assert(rsb.value != ")")
            assert(lsb.value != "(")

            make_sibling(node, rsb.rsb)
            make_sibling(lsb.lsb, node)
            node.parent.removeChild(rsb)
            node.parent.removeChild(lsb)
            rsb.rsb = None
            lsb.lsb = None

            if lsb.kind == "binarylogicop" and lsb.value == node.value:
                for lsbc in lsb.children:
                    make_parentchild(node, lsbc)
                make_parentchild(node, rsb)
                lsbcr = lsb.getRightChild()
                make_sibling(lsbcr, rsb)
            else:
                make_parentchild(node, lsb)
                make_parentchild(node, rsb)
                make_sibling(lsb, rsb)

            # resolve single child of binary operators left as the result of
            # parentheses processing
            if node.parent.kind == "binarylogicop" \
                    and node.parent.value == "-and":
                if node.parent.getNumChildren() == 1:
                    node.grandparent().replaceChild(node.parent, node)

        def attach_flag(node, attach_point_info):
            attach_point = attach_point_info[0]

            if bash.is_double_option(node.word) \
                or is_unary_logic_op(node, attach_point) \
                or node.word in binary_logic_operators \
                or attach_point.value == "find" \
                or len(node.word) <= 1:
                normalize_flag(node, attach_point)
            else:
                # split flags
                assert(node.word.startswith('-'))
                options = node.word[1:]
                if len(options) == 1:
                    normalize_flag(node, attach_point)
                else:
                    str = options + " splitted into: "
                    for option in options:
                        new_node = copy.deepcopy(node)
                        new_node.word = '-' + option
                        normalize_flag(new_node, attach_point)
                        str += new_node.word + ' '
                    if verbose:
                        print(str)

            head_cmd = attach_point.getHeadCommand().value
            flag = node.word
            arg_type = man_lookup.get_flag_arg_type(head_cmd, flag)
            if arg_type:
                # flag is expecting an argument
                attach_point = attach_point.getRightChild()
                return (attach_point, ["argument"], [arg_type])
            else:
                # flag does not take arguments
                return attach_point_info

        def look_above(attach_point):
            head_cmd = attach_point.getHeadCommand()
            return (head_cmd, ["flags", "arguments"], None)

        # Attach point format: (pointer_to_the_attach_point,
        #                       ast_node_type, arg_type)
        attach_point_info = (current, ["headcommand"], [])

        ind = 0
        while ind < len(node.parts):
            attach_point = attach_point_info[0]
            possible_node_kinds = attach_point_info[1]
            possible_arg_types = attach_point_info[2]

            child = node.parts[ind]
            if child.kind == 'word':
                # prioritize processing of logic operators
                if is_unary_logic_op(child, attach_point):
                    norm_node = UnaryLogicOpNode(child.word)
                    attach_to_tree(norm_node, attach_point)
                    unary_logic_ops.append(norm_node)
                elif child.word in binary_logic_operators:
                    if is_binary_logic_op(child, attach_point):
                        norm_node = BinaryLogicOpNode(child.word)
                        attach_to_tree(norm_node, attach_point)
                        binary_logic_ops.append(norm_node)
                    else:
                        attach_point_info = \
                            attach_flag(child, attach_point_info)
                else:
                    if child.word == "--":
                        attach_point_info = (attach_point_info[0],
                                             ["argument"],
                                             attach_point_info[2])
                        ind += 1
                        continue

                    if len(possible_node_kinds) == 1:
                        # no ast_node_kind ambiguation
                        node_kind = possible_node_kinds[0]
                        if node_kind == "headcommand":
                            norm_node = normalize_headcommand(child,
                                                              attach_point)
                            head_commands.append(norm_node)
                            head_cmd = norm_node.value
                            arg_status = copy.deepcopy(man_lookup.get_arg_types(head_cmd))
                            attach_point_info = \
                                (norm_node, ["flag", "argument"], None)
                        elif node_kind == "argument":
                            if possible_arg_types and "Utility" in possible_arg_types:
                                # embedded command leaded by
                                # ["-exec", "-execdir", "-ok", "-okdir"]
                                new_command_node = bast.node(kind="command",
                                                             word="",
                                                             parts=[],
                                                             pos=(-1,-1))
                                # print(new_command_node)
                                new_command_node.parts = []
                                subcommand_added = False
                                for j in xrange(ind, len(node.parts)):
                                    if hasattr(node.parts[j], 'word') \
                                        and (node.parts[j].word == ";" \
                                        or node.parts[j].word == "+"):
                                        normalize_command(new_command_node,
                                                          attach_point)
                                        attach_point.value += \
                                            '::' + node.parts[j].word
                                        subcommand_added = True
                                        break
                                    else:
                                        # print(node.parts[j])
                                        new_command_node.parts.\
                                            append(node.parts[j])
                                if not subcommand_added:
                                    print("Warning: -exec missing ending ';'")
                                    normalize_command(new_command_node,
                                                      attach_point)
                                    attach_point.value += '::' + ";"
                                ind = j
                            else:
                                arg_type = list(possible_arg_types)[0]
                                # recurse to main normalization to handle
                                # argument with deep structures
                                normalize(child, attach_point, "argument",
                                          arg_type)
                            attach_point_info = look_above(attach_point)
                    else:
                        # need to decide ast_node_kind
                        if child.word.startswith("-") \
                            and not (attach_point.value in ["head", "tail"]
                            and child.word[1:].isdigit()):
                            # child is a flag
                            attach_point_info = \
                                attach_flag(child, attach_point_info)
                        else:
                            # child is an argument
                            if expecting("Utility"):
                                # embedded command leaded by
                                # ["sh", "csh", "ksh", "tcsh",
                                #  "zsh", "bash", "exec", "xargs"]
                                new_command_node = bast.node(kind="command",
                                                             word="",
                                                             parts=[],
                                                             pos=(-1,-1))
                                new_command_node.parts = []
                                for j in xrange(ind, len(node.parts)):
                                    new_command_node.parts.append(node.parts[j])
                                normalize_command(new_command_node,
                                                  attach_point)
                                ind = j
                            else:
                                arg_type = cmd_arg_type_check(child.word)
                                # recurse to main normalization to handle argument
                                # with deep structures
                                normalize(child, attach_point, "argument", arg_type)
                            attach_point_info = look_above(attach_point)

            elif child.kind == "assignment":
                normalize(child, attach_point, "assignment")
            elif child.kind == "redirect":
                normalize(child, attach_point, "redirect")

            ind += 1

        # TODO: some commands get parsed with no head command
        # This is usually due to unrecognized utilities e.g. "mp3player".
        if len(head_commands) == 0:
            return

        if len(head_commands) > 1:
            print("Error: multiple headcommands in one command.")
            for hc in head_commands:
                print(hc.symbol)
            sys.exit()

        head_command = head_commands[0]
        # pretty_print(head_command)

        # process (embedded) parenthese -- treat as implicit "-and"
        stack = []
        depth = 0

        def pop_stack_content(depth, rparenth, stack_top=None):
            # popping pushed states off the stack
            popped = stack.pop()
            while (popped.value != "("):
                head_command.removeChild(popped)
                popped = stack.pop()
            lparenth = popped
            if not rparenth:
                # unbalanced brackets
                rparenth = ArgumentNode(value=")")
                make_parentchild(stack_top.parent, rparenth)
                make_sibling(stack_top, rparenth)
            new_child = organize_buffer(lparenth, rparenth)
            i = head_command.substituteParentheses(lparenth, rparenth,
                                                   new_child)
            depth -= 1
            if depth > 0:
                # embedded parenthese
                stack.append(new_child)
            return depth, i

        i = 0
        while i < head_command.getNumChildren():
            child = head_command.children[i]
            if child.value == "(":
                stack.append(child)
                depth += 1
            elif child.value == ")":
                assert(depth >= 0)
                # fix imbalanced parentheses: missing '('
                if depth == 0:
                    # simply drop the single ')'
                    detach_from_tree(child, child.parent)
                else:
                    depth, i = pop_stack_content(depth, child)
            else:
                if depth > 0:
                    stack.append(child)
                else:
                    if child.kind == "unarylogicop":
                        unprocessed_unary_logic_ops.append(child)
                    if child.kind == "binarylogicop":
                        unprocessed_binary_logic_ops.append(child)
            i += 1

        # fix imbalanced parentheses: missing ')'
        while (depth > 0):
            depth, _ = pop_stack_content(depth, None, stack[-1])

        assert(len(stack) == 0)
        assert(depth == 0)

        for ul in unprocessed_unary_logic_ops:
            adjust_unary_operators(ul)

        for bl in unprocessed_binary_logic_ops:
            adjust_binary_operators(bl)

        # recover omitted arguments
        if head_command.value == "find":
            arguments = []
            for child in head_command.children:
                if child.kind == "argument":
                    arguments.append(child)
            if head_command.getNumChildren() > 0 and len(arguments) < 1:
                norm_node = ArgumentNode(value=".", arg_type="File")
                make_sibling(norm_node, head_command.children[0])
                norm_node.parent = head_command
                head_command.children.insert(0, norm_node)

    def normalize(node, current, node_kind="", arg_type=""):
        # recursively normalize each subtree
        if not type(node) is bast.node:
            raise ValueError('type(node) is not ast.node')
        if node.kind == 'word':
            # assign fine-grained types
            if node.parts:
                # Compound arguments
                # commandsubstitution, processsubstitution, parameter
                if node.parts[0].kind == "processsubstitution":
                    if '>' in node.word:
                        norm_node = ProcessSubstitutionNode('>')
                        attach_to_tree(norm_node, current)
                        for child in node.parts:
                            normalize(child, norm_node)
                    elif '<' in node.word:
                        norm_node = ProcessSubstitutionNode('<')
                        attach_to_tree(norm_node, current)
                        for child in node.parts:
                            normalize(child, norm_node)
                elif node.parts[0].kind == "commandsubstitution":
                    norm_node = CommandSubstitutionNode()
                    attach_to_tree(norm_node, current)
                    for child in node.parts:
                        normalize(child, norm_node)
                elif node.parts[0].kind == "parameter" or \
                    node.parts[0].kind == "tilde":
                    normalize_argument(node, current, arg_type)
                else:
                    for child in node.parts:
                        normalize(child, current)
            else:
                normalize_argument(node, current, arg_type)
        elif node.kind == "pipeline":
            norm_node = PipelineNode()
            attach_to_tree(norm_node, current)
            if len(node.parts) % 2 == 0:
                print("Error: pipeline node must have odd number of parts")
                print(node)
                sys.exit()
            for child in node.parts:
                if child.kind == "command":
                    normalize(child, norm_node)
                elif child.kind == "pipe":
                    pass
                else:
                    raise ValueError(
                        "Error: unrecognized type of child of pipeline node")
        elif node.kind == "list":
            if len(node.parts) > 2:
                # multiple commands, not supported
                raise ValueError("Unsupported: list of length >= 2")
            else:
                normalize(node.parts[0], current)
        elif node.kind == "commandsubstitution" or \
             node.kind == "processsubstitution":
            normalize(node.command, current)
        elif node.kind == "command":
            try:
                normalize_command(node, current)
            except AssertionError, e:
                raise AssertionError("normalized_command AssertionError")
        elif hasattr(node, 'parts'):
            for child in node.parts:
                # skip current node
                normalize(child, current)
        elif node.kind == "redirect":
            # not supported
            raise ValueError("Unsupported: %s" % node.kind)
        elif node.kind == "operator":
            # not supported
            raise ValueError("Unsupported: %s" % node.kind)
        elif node.kind == "parameter":
            # not supported
            raise ValueError("Unsupported: parameters")
        elif node.kind == "for":
            # not supported
            raise ValueError("Unsupported: %s" % node.kind)
        elif node.kind == "if":
            # not supported
            raise ValueError("Unsupported: %s" % node.kind)
        elif node.kind == "while":
            # not supported
            raise ValueError("Unsupported: %s" % node.kind)
        elif node.kind == "until":
            # not supported
            raise ValueError("Unsupported: %s" % node.kind)
        elif node.kind == "assignment":
            # not supported
            raise ValueError("Unsupported: %s" % node.kind)
        elif node.kind == "function":
            # not supported
            raise ValueError("Unsupported: %s" % node.kind)
        elif node.kind == "tilde":
            # not supported
            raise ValueError("Unsupported: %s" % node.kind)
        elif node.kind == "heredoc":
            # not supported
            raise ValueError("Unsupported: %s" % node.kind)

    try:
        cmd2 = cmd.encode('utf-8')
    except UnicodeDecodeError, e:
        cmd2 = cmd

    try:
        tree = bparser.parse(cmd)
    except tokenizer.MatchedPairError, e:
        print("Cannot parse: %s - MatchedPairError" % cmd2)
        # return basic_tokenizer(cmd, normalize_digits, False)
        return None
    except errors.ParsingError, e:
        print("Cannot parse: %s - ParsingError" % cmd2)
        # return basic_tokenizer(cmd, normalize_digits, False)
        return None
    except NotImplementedError, e:
        print("Cannot parse: %s - NotImplementedError" % cmd2)
        # return basic_tokenizer(cmd, normalize_digits, False)
        return None
    except IndexError, e:
        print("Cannot parse: %s - IndexError" % cmd2)
        # empty command
        return None
    except AttributeError, e:
        print("Cannot parse: %s - AttributeError" % cmd2)
        # not a bash command
        return None

    if len(tree) > 1:
        print("Doesn't support command with multiple root nodes: %s" % cmd2)
    normalized_tree = Node(kind="root")
    try:
        normalize(tree[0], normalized_tree)
    except ValueError as err:
        print("%s - %s" % (err.args[0], cmd2))
        return None
    except AttributeError as err:
        print("%s - %s" % (err.args[0], cmd2))
        return None
    except AssertionError as err:
        print("%s - %s" % (err.args[0], cmd2))
        return None

    return normalized_tree


def prune_ast(node):
    """Return an ast without the argument nodes."""
    def prune_ast_fun(node):
        to_remove = []
        for child in node.children:
            if child.kind == "argument" and \
                not child.arg_type == "ReservedWord":
                    if child.lsb:
                        child.lsb.rsb = child.rsb
                    if child.rsb:
                        child.rsb.lsb = child.lsb
                    to_remove.append(child)
            else:
                prune_ast_fun(child)
        for child in to_remove:
            node.removeChild(child)
    if not node:
        return None
    node = copy.deepcopy(node)
    prune_ast_fun(node)

    return node


def list_to_ast(list, order='dfs'):
    root = Node(kind="root", value="root")
    current = root
    if order == 'dfs':
        for i in xrange(1, len(list)):
            if not current:
                break
            symbol = list[i]
            if symbol in [_V_NO_EXPAND, _H_NO_EXPAND]:
                current = current.parent
            else:
                kind, value = symbol.split('_', 1)
                kind = kind.lower()
                # add argument types
                if kind == "argument":
                    if current.kind == "flag":
                        head_cmd = current.getHeadCommand().value
                        flag = current.value
                        arg_type = man_lookup.get_flag_arg_type(head_cmd, flag)
                    elif current.kind == "headcommand":
                        head_cmd = current.value
                        arg_type = type_check(value,
                                              man_lookup.get_arg_types(head_cmd))
                    else:
                        print("Warning: to_ast unrecognized argument "
                              "attachment point {}.".format(current.symbol))
                        arg_type = "Unknown"
                    node = ArgumentNode(value=value, arg_type=arg_type)
                elif kind == "flag":
                    node = FlagNode(value=value)
                elif kind == "headcommand":
                    node = HeadCommandNode(value=value)
                elif kind == "unarylogicop":
                    node = UnaryLogicOpNode(value=value)
                else:
                    node = Node(kind=kind, value=value)
                attach_to_tree(node, current)
                current = node
    else:
        raise NotImplementedError
    return root


def to_tokens(node, loose_constraints=False, ignore_flag_order=False,
              arg_type_only=False, with_arg_type=False):
    if not node:
        return []

    lc = loose_constraints
    ifo = ignore_flag_order
    ato = arg_type_only
    wat = with_arg_type

    def to_tokens_fun(node):
        tokens = []
        if node.kind == "root":
            try:
                assert(loose_constraints or node.getNumChildren() == 1)
            except AssertionError, e:
                return []
            if lc:
                for child in node.children:
                    tokens += to_tokens_fun(child)
            else:
                tokens = to_tokens_fun(node.children[0])
        elif node.kind == "pipeline":
            assert(loose_constraints or node.getNumChildren() > 1)
            if lc and node.getNumChildren() < 1:
                tokens.append("|")
            elif lc and node.getNumChildren() == 1:
                # treat "single-pipe" as atomic command
                tokens += to_tokens_fun(node.children[0])
            else:
                for child in node.children[:-1]:
                    tokens += to_tokens_fun(child)
                    tokens.append("|")
                tokens += to_tokens_fun(node.children[-1])
        elif node.kind == "commandsubstitution":
            assert(loose_constraints or node.getNumChildren() == 1)
            if lc and node.getNumChildren() < 1:
                tokens += ["$(", ")"]
            else:
                tokens.append("$(")
                tokens += to_tokens_fun(node.children[0])
                tokens.append(")")
        elif node.kind == "processsubstitution":
            assert(loose_constraints or node.getNumChildren() == 1)
            if lc and node.getNumChildren() < 1:
                tokens.append(node.value + "(")
                tokens.append(")")
            else:
                tokens.append(node.value + "(")
                tokens += to_tokens_fun(node.children[0])
                tokens.append(")")
        elif node.kind == "headcommand":
            tokens.append(node.value)
            children = sorted(node.children, key=lambda x:x.value) \
                if ifo else node.children
            for child in children:
                tokens += to_tokens_fun(child)
        elif node.kind == "flag":
            if '::' in node.value:
                value, op = node.value.split('::')
                tokens.append(value)
            else:
                tokens.append(node.value)
            for child in node.children:
                tokens += to_tokens_fun(child)
            if '::' in node.value:
                if op == ';':
                    op = "\\;"
                tokens.append(op)
        elif node.kind == "binarylogicop":
            assert(loose_constraints or node.getNumChildren() > 1)
            if lc and node.getNumChildren() < 2:
                for child in node.children:
                    tokens += to_tokens_fun(child)
            else:
                tokens.append("\\(")
                for i in xrange(len(node.children)-1):
                    tokens += to_tokens_fun(node.children[i])
                    tokens.append(node.value)
                tokens += to_tokens_fun(node.children[-1])
                tokens.append("\\)")
        elif node.kind == "unarylogicop":
            assert((loose_constraints or node.associate == UnaryLogicOpNode.LEFT)
                   or node.getNumChildren() == 1)
            if lc and node.getNumChildren() < 1:
                tokens.append(node.value)
            else:
                if node.associate == UnaryLogicOpNode.RIGHT:
                    tokens.append(node.value)
                    tokens += to_tokens_fun(node.children[0])
                else:
                    if node.getNumChildren() > 0:
                        tokens += to_tokens_fun(node.children[0])
                    tokens.append(node.value)
        elif node.kind == "argument":
            assert(loose_constraints or node.getNumChildren() == 0)
            if wat:
                tokens.append(node.symbol)
            elif ato and not node.arg_type == "ReservedWord":
                if loose_constraints and not node.arg_type:
                    tokens.append("Unknown")
                else:
                    tokens.append(node.arg_type)
            else:
                tokens.append(node.value)
            if lc:
                for child in node.children:
                    tokens += to_tokens_fun(child)
        return tokens

    return to_tokens_fun(node)
