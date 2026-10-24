# -*- coding: utf-8 -*-
"""SimaticML generator for S7-1200 LAD blocks (TIA Portal V19).

The output is the Openness import format (``SW.Blocks.*`` documents).  Only the
subset needed by the traffic-light project is implemented, but the builder is
generic enough to extend.

Verified facts taken from a real V19 export (see _probe/OB_Main.xml):
  * ``<Engineering version="V19" />``
  * interface namespace  -> .../SW/Interface/v5
  * multilingual culture -> zh-CN
  * an empty OB carries one CompileUnit with an empty <NetworkSource />
"""

NS_IF = "http://www.siemens.com/automation/Openness/SW/Interface/v5"
NS_FLG = "http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v5"
NS_FLG4 = "http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4"


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# --------------------------------------------------------------------------- #
#  token helpers for wire endpoints
# --------------------------------------------------------------------------- #
RAIL = ("rail",)
BRANCH = ("branch",)


def PIN(uid, name):
    return ("pin", uid, name)


def IDENT(uid):
    return ("ident", uid)


def OPEN(uid):
    return ("open", uid)


def _render_end(tok):
    kind = tok[0]
    if kind == "rail":
        return "<Powerrail />"
    if kind == "branch":
        # parallel-branch merge point; carries no UId of its own
        return "<Openbranch />"
    if kind == "pin":
        return '<NameCon UId="%d" Name="%s" />' % (tok[1], esc(tok[2]))
    if kind == "ident":
        return '<IdentCon UId="%d" />' % tok[1]
    if kind == "open":
        return '<OpenCon UId="%d" />' % tok[1]
    raise ValueError("bad token %r" % (tok,))


# --------------------------------------------------------------------------- #
#  one LAD network
# --------------------------------------------------------------------------- #
class Net(object):
    """Builds a single FlgNet network using a cursor/branch model."""

    def __init__(self, title="", comment=""):
        self.title = title
        self.comment = comment
        self._uid = 10
        self.parts = []        # list of dicts: uid/name/negated/instance
        self.accesses = []     # list of xml strings
        self.wires = []        # list of (src, dst)
        self.cur = [RAIL]      # active source nodes for the next element
        self._branch_start = None
        self._branch_ends = []

    # -- primitives ---------------------------------------------------------
    def _u(self):
        self._uid += 1
        return self._uid

    def _part(self, name, negated=None, instance=None, templates=None):
        uid = self._u()
        self.parts.append({"uid": uid, "name": name,
                           "negated": list(negated or []),
                           "instance": instance,
                           "templates": list(templates or [])})
        return uid

    def _wire(self, src, dst):
        # a bare int is an access/operand uid -> IdentCon
        if isinstance(src, int):
            src = IDENT(src)
        if isinstance(dst, int):
            dst = IDENT(dst)
        self.wires.append((src, dst))

    def _feed(self, uid, pin):
        """wire the current cursor(s) into ``pin`` of part ``uid``."""
        for s in self.cur:
            self._wire(s, PIN(uid, pin))

    # -- operands -----------------------------------------------------------
    def g(self, name):
        """global tag -> access uid"""
        uid = self._u()
        comps = "".join('<Component Name="%s" />' % esc(c) for c in name.split("."))
        self.accesses.append(
            '<Access Scope="GlobalVariable" UId="%d"><Symbol>%s</Symbol></Access>'
            % (uid, comps))
        return uid

    def loc(self, name):
        """local (interface) variable -> access uid"""
        uid = self._u()
        self.accesses.append(
            '<Access Scope="LocalVariable" UId="%d"><Symbol><Component Name="%s" />'
            '</Symbol></Access>' % (uid, esc(name)))
        return uid

    def locm(self, name, *members):
        """local variable member, e.g. locm('T1', 'Q') -> T1.Q"""
        uid = self._u()
        comps = "".join('<Component Name="%s" />' % esc(c)
                        for c in (name,) + members)
        self.accesses.append(
            '<Access Scope="LocalVariable" UId="%d"><Symbol>%s</Symbol></Access>'
            % (uid, comps))
        return uid

    def lit(self, value, ctype="Int"):
        uid = self._u()
        self.accesses.append(
            '<Access Scope="LiteralConstant" UId="%d"><Constant>'
            '<ConstantType>%s</ConstantType><ConstantValue>%s</ConstantValue>'
            '</Constant></Access>' % (uid, esc(ctype), esc(value)))
        return uid

    # -- boolean chain ------------------------------------------------------
    def _contact(self, sym_uid, neg):
        uid = self._part("Contact", negated=(["operand"] if neg else None))
        self._feed(uid, "in")
        self._wire(PIN(uid, "operand"), IDENT(sym_uid))
        self.cur = [PIN(uid, "out")]
        return uid

    def ld(self, name):
        return self._contact(self.g(name), False)

    def ldn(self, name):
        return self._contact(self.g(name), True)

    def ldl(self, name):
        return self._contact(self.loc(name), False)

    def ldnl(self, name):
        return self._contact(self.loc(name), True)

    def ldlm(self, name, *members):
        return self._contact(self.locm(name, *members), False)

    def ldnlm(self, name, *members):
        return self._contact(self.locm(name, *members), True)

    def _coil(self, sym_uid, kind):
        uid = self._part(kind)
        self._feed(uid, "in")
        self._wire(PIN(uid, "operand"), IDENT(sym_uid))
        self.cur = [PIN(uid, "out")]
        return uid

    def out(self, name):
        return self._coil(self.g(name), "Coil")

    def outl(self, name):
        return self._coil(self.loc(name), "Coil")

    def set(self, name):
        uid = self._part("SCoil")
        self._feed(uid, "in")
        self._wire(PIN(uid, "operand"), IDENT(self.g(name)))
        self.cur = [PIN(uid, "out")]
        return uid

    def rst(self, name):
        uid = self._part("RCoil")
        self._feed(uid, "in")
        self._wire(PIN(uid, "operand"), IDENT(self.g(name)))
        self.cur = [PIN(uid, "out")]
        return uid

    def setl(self, name):
        uid = self._part("SCoil")
        self._feed(uid, "in")
        self._wire(PIN(uid, "operand"), IDENT(self.loc(name)))
        self.cur = [PIN(uid, "out")]
        return uid

    def rsl(self, name):
        uid = self._part("RCoil")
        self._feed(uid, "in")
        self._wire(PIN(uid, "operand"), IDENT(self.loc(name)))
        self.cur = [PIN(uid, "out")]
        return uid

    # -- parallel branches --------------------------------------------------
    def par(self):
        """start an OR group: remember where the first branch begins."""
        self._branch_start = list(self.cur)
        self._branch_ends = []

    def also(self):
        """start another parallel branch from the group's entry point."""
        self._branch_ends.extend(self.cur)
        self.cur = list(self._branch_start)

    def endpar(self):
        """merge all parallel branches: every branch end feeds the next element."""
        self._branch_ends.extend(self.cur)
        self.cur = list(self._branch_ends)

    # -- boxes --------------------------------------------------------------
    def _box_bool(self, part, pins_in, sym_operand=None, neg_pins=None):
        """generic boolean box: pins_in = [(pin, token)]"""
        uid = self._part(part, negated=neg_pins)
        for pin, tok in pins_in:
            self._wire(tok, PIN(uid, pin))
        if sym_operand:
            self._wire(PIN(uid, "operand"), sym_operand)
        self.cur = [PIN(uid, "out")]
        return uid

    def cmp(self, op, a_tok, b_tok, src_type="Int"):
        uid = self._part(op, templates=[("SrcType", "Type", src_type)])
        for tok in self.cur:
            self._wire(tok, PIN(uid, "pre"))
        self._wire(a_tok, PIN(uid, "in1"))
        self._wire(b_tok, PIN(uid, "in2"))
        self.cur = [PIN(uid, "out")]
        return uid

    def ton(self, instance_name, pt_tok, scope="LocalVariable", time_type="Time"):
        """TON box. time_type is a generic parameter (Time / S5Time / LTime)."""
        uid = self._part("TON", instance=("TON", scope, instance_name),
                         templates=[("time_type", "Type", time_type)])
        for tok in self.cur:
            self._wire(tok, PIN(uid, "IN"))
        self._wire(pt_tok, PIN(uid, "PT"))
        # every TON pin must be resolved; ET is left dangling on purpose
        self._wire(PIN(uid, "ET"), OPEN(self._u()))
        self.cur = [PIN(uid, "Q")]
        return uid

    def move(self, src_tok, dst_tok, card=1):
        uid = self._part("Move", templates=[("Card", "Cardinality", card)])
        for tok in self.cur:
            self._wire(tok, PIN(uid, "en"))
        self._wire(src_tok, PIN(uid, "in"))
        self._wire(dst_tok, PIN(uid, "out1"))
        self.cur = [PIN(uid, "eno")]
        return uid

    def add(self, a_tok, b_tok, dst_tok, src_type="Int"):
        tpls = [("SrcType", "Type", src_type)]
        if src_type != "Time":
            tpls.append(("Card", "Cardinality", 2))
        uid = self._part("Add", templates=tpls)
        for tok in self.cur:
            self._wire(tok, PIN(uid, "en"))
        self._wire(a_tok, PIN(uid, "in1"))
        self._wire(b_tok, PIN(uid, "in2"))
        self._wire(dst_tok, PIN(uid, "out"))
        self.cur = [PIN(uid, "eno")]
        return uid

    def sub(self, a_tok, b_tok, dst_tok, src_type="Int"):
        tpls = [("SrcType", "Type", src_type)]
        uid = self._part("Sub", templates=tpls)
        for tok in self.cur:
            self._wire(tok, PIN(uid, "en"))
        self._wire(a_tok, PIN(uid, "in1"))
        self._wire(b_tok, PIN(uid, "in2"))
        self._wire(dst_tok, PIN(uid, "out"))
        self.cur = [PIN(uid, "eno")]
        return uid

    # -- block call ---------------------------------------------------------
    def call_fb(self, block_name, db_name, params=None):
        """Call an FB instance (LAD call box)."""
        uid = self._u()
        inst_uid = self._u()
        parts = ['<Call UId="%d"><CallInfo Name="%s" BlockType="FB">'
                 % (uid, esc(block_name)),
                 '<Instance Scope="GlobalVariable" UId="%d"><Component Name="%s" />'
                 '</Instance>' % (inst_uid, esc(db_name))]
        for pname, tok in (params or {}).items():
            if isinstance(tok, int):
                tok = IDENT(tok)
            parts.append('<Parameter Name="%s">%s</Parameter>'
                         % (esc(pname), _render_end(tok)))
        parts.append("</CallInfo></Call>")
        self.parts.append({"uid": uid, "raw": "".join(parts)})
        self._wire(RAIL, PIN(uid, "EN"))
        self.cur = [PIN(uid, "ENO")]
        return uid

    # -- serialisation ------------------------------------------------------
    def xml(self):
        parts = []
        for a in self.accesses:
            parts.append(a)
        for p in self.parts:
            if p.get("raw"):
                parts.append(p["raw"])
                continue
            inst = ""
            if p["instance"]:
                _, scope, name = p["instance"]
                inst = ('<Instance Scope="%s" UId="%d"><Component Name="%s" /></Instance>'
                        % (scope, self._u(), esc(name)))
            tpl = "".join('<TemplateValue Name="%s" Type="%s">%s</TemplateValue>'
                          % (esc(t[0]), esc(t[1]), esc(t[2]))
                          for t in p["templates"])
            neg = "".join('<Negated Name="%s" />' % esc(n) for n in p["negated"])
            parts.append('<Part Name="%s" UId="%d">%s%s%s</Part>'
                         % (p["name"], p["uid"], inst, tpl, neg))
        wires = []
        for i, (src, dst) in enumerate(self.wires):
            wires.append('<Wire UId="%d">%s%s</Wire>'
                         % (1000 + i + 1, _render_end(src), _render_end(dst)))
        return ('<FlgNet xmlns="%s"><Parts>%s</Parts><Wires>%s</Wires></FlgNet>'
                % (NS_FLG, "".join(parts), "".join(wires)))


# --------------------------------------------------------------------------- #
#  blocks
# --------------------------------------------------------------------------- #
SECTION_ORDER = {
    "FB": ["Input", "Output", "InOut", "Static", "Temp", "Constant"],
    "FC": ["Input", "Output", "InOut", "Temp", "Constant", "Return"],
    "OB": ["Input", "Temp", "Constant"],
}


def _member(name, datatype, start=None, comment=None, extra=None):
    attrs = 'Name="%s" Datatype="%s" Accessibility="Public"' % (esc(name), esc(datatype))
    # IEC timer / counter instances carry a version in TIA's own export
    if datatype in ("TON_TIME", "TOF_TIME", "TP_TIME", "TONR_TIME",
                    "CTU", "CTD", "CTUD"):
        attrs += ' Version="1.0"'
    if extra:
        attrs += " " + extra
    inner = ""
    if start is not None and start != "":
        # TIA writes the default value as a child element, not as an attribute
        inner += "<StartValue>%s</StartValue>" % esc(start)
    if comment:
        inner += ('<Comment><MultiLanguageText Lang="zh-CN">%s</MultiLanguageText>'
                  '</Comment>' % esc(comment))
    if inner:
        return "<Member %s>%s</Member>" % (attrs, inner)
    return "<Member %s />" % attrs


class Block(object):
    def __init__(self, kind, name, number, interface=None, title="", comment="",
                 flg_ns=NS_FLG):
        """kind: 'FB' | 'FC' | 'OB'.  interface: {section: [(name, type, start)]}"""
        self.kind = kind
        self.name = name
        self.number = number
        self.interface = interface or {}
        self.title = title
        self.comment = comment
        self.nets = []
        self.flg_ns = flg_ns

    def net(self, title="", comment=""):
        n = Net(title, comment)
        self.nets.append(n)
        return n

    def _sections_xml(self):
        out = ['<Sections xmlns="%s">' % NS_IF]
        for sec in SECTION_ORDER[self.kind]:
            members = self.interface.get(sec) or []
            if not members:
                out.append('  <Section Name="%s" />' % sec)
                continue
            out.append('  <Section Name="%s">' % sec)
            for m in members:
                nm, dt = m[0], m[1]
                st = m[2] if len(m) > 2 else None
                cm = m[3] if len(m) > 3 else None
                ex = m[4] if len(m) > 4 else None
                out.append("    " + _member(nm, dt, st, cm, ex))
            out.append("  </Section>")
        out.append("</Sections>")
        return "\n".join(out)

    def xml(self):
        uid = [100]

        def nid():
            uid[0] += 1
            return uid[0]

        tag = "SW.Blocks.%s" % ("OB" if self.kind == "OB" else
                                ("FB" if self.kind == "FB" else "FC"))
        L = []
        L.append('<?xml version="1.0" encoding="utf-8"?>')
        L.append("<Document>")
        L.append('  <Engineering version="V19" />')
        L.append('  <%s ID="0">' % tag)
        L.append("    <AttributeList>")
        L.append("      <AutoNumber>true</AutoNumber>")
        L.append("      <HeaderAuthor />")
        L.append("      <HeaderFamily />")
        L.append("      <HeaderName />")
        L.append("      <HeaderVersion>0.1</HeaderVersion>")
        L.append("      <Interface>%s</Interface>" % self._sections_xml())
        L.append("      <IsIECCheckEnabled>false</IsIECCheckEnabled>")
        L.append("      <MemoryLayout>Optimized</MemoryLayout>")
        L.append("      <Name>%s</Name>" % esc(self.name))
        L.append("      <Namespace />")
        L.append("      <Number>%d</Number>" % self.number)
        L.append("      <ProgrammingLanguage>LAD</ProgrammingLanguage>")
        if self.kind == "OB":
            L.append("      <SecondaryType>ProgramCycle</SecondaryType>")
        L.append("      <SetENOAutomatically>false</SetENOAutomatically>")
        L.append("    </AttributeList>")
        L.append("    <ObjectList>")

        # block comment
        cid, cid2 = nid(), nid()
        L.append('      <MultilingualText ID="%d" CompositionName="Comment">' % cid)
        L.append("        <ObjectList>")
        L.append('          <MultilingualTextItem ID="%d" CompositionName="Items">' % cid2)
        L.append("            <AttributeList>")
        L.append("              <Culture>zh-CN</Culture>")
        if self.comment:
            L.append("              <Text>%s</Text>" % esc(self.comment))
        else:
            L.append("              <Text />")
        L.append("            </AttributeList>")
        L.append("          </MultilingualTextItem>")
        L.append("        </ObjectList>")
        L.append("      </MultilingualText>")

        # networks
        for n in self.nets:
            cu = nid()
            L.append('      <SW.Blocks.CompileUnit ID="%d" CompositionName="CompileUnits">' % cu)
            L.append("        <AttributeList>")
            src = n.xml().replace('<FlgNet xmlns="%s"' % NS_FLG,
                                 '<FlgNet xmlns="%s"' % self.flg_ns)
            L.append("          <NetworkSource>%s</NetworkSource>" % src)
            L.append("          <ProgrammingLanguage>LAD</ProgrammingLanguage>")
            L.append("        </AttributeList>")
            L.append("        <ObjectList>")
            for label, text in (("Comment", n.comment), ("Title", n.title)):
                i1, i2 = nid(), nid()
                L.append('          <MultilingualText ID="%d" CompositionName="%s">' % (i1, label))
                L.append("            <ObjectList>")
                L.append('              <MultilingualTextItem ID="%d" CompositionName="Items">' % i2)
                L.append("                <AttributeList>")
                L.append("                  <Culture>zh-CN</Culture>")
                if text:
                    L.append("                  <Text>%s</Text>" % esc(text))
                else:
                    L.append("                  <Text />")
                L.append("                </AttributeList>")
                L.append("              </MultilingualTextItem>")
                L.append("            </ObjectList>")
                L.append("          </MultilingualText>")
            L.append("        </ObjectList>")
            L.append("      </SW.Blocks.CompileUnit>")

        # block title
        t1, t2 = nid(), nid()
        L.append('      <MultilingualText ID="%d" CompositionName="Title">' % t1)
        L.append("        <ObjectList>")
        L.append('          <MultilingualTextItem ID="%d" CompositionName="Items">' % t2)
        L.append("            <AttributeList>")
        L.append("              <Culture>zh-CN</Culture>")
        if self.title:
            L.append('              <Text>"%s"</Text>' % esc(self.title))
        else:
            L.append("              <Text />")
        L.append("            </AttributeList>")
        L.append("          </MultilingualTextItem>")
        L.append("        </ObjectList>")
        L.append("      </MultilingualText>")

        L.append("    </ObjectList>")
        L.append("  </%s>" % tag)
        L.append("</Document>")
        return "\n".join(L)
