##
# Copyright (c) 2006-2016 Apple Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##

from txdav.common.datastore.query import expression

"""
SQLLite statement generator from query expressions.
"""

__all__ = [
    "sqllitegenerator",
]

import cStringIO as StringIO

class sqllitegenerator(object):

    FROM = " from "
    WHERE = " where "
    RESOURCEDB = "RESOURCE"
    TIMESPANDB = "TIMESPAN"
    TRANSPARENCYDB = "TRANSPARENCY"
    PERUSERDB = "PERUSER"
    NOTOP = "NOT "
    ANDOP = " AND "
    OROP = " OR "
    CONTAINSOP = " GLOB "
    NOTCONTAINSOP = " NOT GLOB "
    ISOP = " == "
    ISNOTOP = " != "
    STARTSWITHOP = " GLOB "
    NOTSTARTSWITHOP = " NOT GLOB "
    ENDSWITHOP = " GLOB "
    NOTENDSWITHOP = " NOT GLOB "
    INOP = " IN "
    NOTINOP = " NOT IN "

    FIELDS = {
        "TYPE": "RESOURCE.TYPE",
        "UID": "RESOURCE.UID",
    }

    TIMESPANTEST = "((TIMESPAN.FLOAT == 'N' AND TIMESPAN.START < %s AND TIMESPAN.END > %s) OR (TIMESPAN.FLOAT == 'Y' AND TIMESPAN.START < %s AND TIMESPAN.END > %s))"
    TIMESPANTEST_NOEND = "((TIMESPAN.FLOAT == 'N' AND TIMESPAN.END > %s) OR (TIMESPAN.FLOAT == 'Y' AND TIMESPAN.END > %s))"
    TIMESPANTEST_NOSTART = "((TIMESPAN.FLOAT == 'N' AND TIMESPAN.START < %s) OR (TIMESPAN.FLOAT == 'Y' AND TIMESPAN.START < %s))"
    TIMESPANTEST_TAIL_PIECE = " AND TIMESPAN.RESOURCEID == RESOURCE.RESOURCEID"
    TIMESPANTEST_JOIN_ON_PIECE = "TIMESPAN.INSTANCEID == TRANSPARENCY.INSTANCEID AND TRANSPARENCY.PERUSERID == %s"

    def __init__(self, expr, calendarid, userid, freebusy=False):
        """

        @param expr: the query expression object model
        @type expr: L{Filter}
        @param calendarid: resource ID - not used for file-based per-calendar indexes
        @type calendarid: C{int}
        @param userid: user for whom query is being done - query will be scoped to that user's privileges and their transparency
        @type userid: C{str}
        @param freebusy: whether or not a freebusy query is being done - if it is, additional time range and transparency information is returned
        @type freebusy: C{bool}
        """
        self.expression = expr
        self.calendarid = calendarid
        self.userid = userid if userid else ""
        self.freebusy = freebusy
        self.usedtimespan = False


    def generate(self):
        """
        Generate the actual SQL 'where ...' expression from the passed in expression tree.

        @return: a C{tuple} of (C{str}, C{list}), where the C{str} is the partial SQL statement,
            and the C{list} is the list of argument substitutions to use with the SQL API execute method.
        """

        # Init state
        self.sout = StringIO.StringIO()
        self.arguments = []
        self.substitutions = []
        self.usedtimespan = False

        # Generate ' where ...' partial statement
        self.generateExpression(self.expression)

        # Prefix with ' from ...' partial statement
        select = self.FROM + self.RESOURCEDB
        if self.usedtimespan:

            # Free busy needs transparency join
            if self.freebusy:
                self.frontArgument(self.userid)
                select += ", %s LEFT OUTER JOIN %s ON (%s)" % (
                    self.TIMESPANDB,
                    self.TRANSPARENCYDB,
                    self.TIMESPANTEST_JOIN_ON_PIECE
                )
            else:
                select += ", %s" % (
                    self.TIMESPANDB,
                )
        select += self.WHERE
        if self.usedtimespan:
            select += "("
        select += self.sout.getvalue()
        if self.usedtimespan:
            if self.calendarid:
                self.setArgument(self.calendarid)
            select += ")%s" % (self.TIMESPANTEST_TAIL_PIECE,)

        select = select % tuple(self.substitutions)

        return select, self.arguments


    def generateExpression(self, expr):
        """
        Generate an expression and all it's subexpressions.

        @param expr: the L{baseExpression} derived class to write out.
        @return: C{True} if the TIMESPAN table is used, C{False} otherwise.
        """

        # Generate based on each type of expression we might encounter

        # ALL
        if isinstance(expr, expression.allExpression):
            # Wipe out the ' where ...' clause so everything is matched
            self.sout.truncate(0)
            self.arguments = []
            self.substitutions = []
            self.usedtimespan = False

        # NOT
        elif isinstance(expr, expression.notExpression):
            self.sout.write(self.NOTOP)
            self.generateSubExpression(expr.expressions[0])

        # AND
        elif isinstance(expr, expression.andExpression):
            first = True
            for e in expr.expressions:
                if first:
                    first = False
                else:
                    self.sout.write(self.ANDOP)
                self.generateSubExpression(e)

        # OR
        elif isinstance(expr, expression.orExpression):
            first = True
            for e in expr.expressions:
                if first:
                    first = False
                else:
                    self.sout.write(self.OROP)
                self.generateSubExpression(e)

        # time-range
        elif isinstance(expr, expression.timerangeExpression):
            if expr.start and expr.end:
                self.setArgument(expr.end)
                self.setArgument(expr.start)
                self.setArgument(expr.endfloat)
                self.setArgument(expr.startfloat)
                test = self.TIMESPANTEST
            elif expr.start and expr.end is None:
                self.setArgument(expr.start)
                self.setArgument(expr.startfloat)
                test = self.TIMESPANTEST_NOEND
            elif not expr.start and expr.end:
                self.setArgument(expr.end)
                self.setArgument(expr.endfloat)
                test = self.TIMESPANTEST_NOSTART

            self.sout.write(test)
            self.usedtimespan = True

        # CONTAINS
        elif isinstance(expr, expression.containsExpression):
            self.sout.write(expr.field)
            self.sout.write(self.CONTAINSOP)
            self.addArgument(self.containsArgument(expr.text))

        # NOT CONTAINS
        elif isinstance(expr, expression.notcontainsExpression):
            self.sout.write(expr.field)
            self.sout.write(self.NOTCONTAINSOP)
            self.addArgument(self.containsArgument(expr.text))

        # IS
        elif isinstance(expr, expression.isExpression):
            self.sout.write(expr.field)
            self.sout.write(self.ISOP)
            self.addArgument(expr.text)

        # IS NOT
        elif isinstance(expr, expression.isnotExpression):
            self.sout.write(expr.field)
            self.sout.write(self.ISNOTOP)
            self.addArgument(expr.text)

        # STARTSWITH
        elif isinstance(expr, expression.startswithExpression):
            self.sout.write(expr.field)
            self.sout.write(self.STARTSWITHOP)
            self.addArgument(self.startswithArgument(expr.text))

        # NOT STARTSWITH
        elif isinstance(expr, expression.notstartswithExpression):
            self.sout.write(expr.field)
            self.sout.write(self.NOTSTARTSWITHOP)
            self.addArgument(self.startswithArgument(expr.text))

        # ENDSWITH
        elif isinstance(expr, expression.endswithExpression):
            self.sout.write(expr.field)
            self.sout.write(self.ENDSWITHOP)
            self.addArgument(self.endswithArgument(expr.text))

        # NOT ENDSWITH
        elif isinstance(expr, expression.notendswithExpression):
            self.sout.write(expr.field)
            self.sout.write(self.NOTENDSWITHOP)
            self.addArgument(self.endswithArgument(expr.text))

        # IN
        elif isinstance(expr, expression.inExpression):
            self.sout.write(expr.field)
            self.sout.write(self.INOP)
            self.sout.write("(")
            for count, item in enumerate(expr.text):
                if count != 0:
                    self.sout.write(", ")
                self.addArgument(item)
            self.sout.write(")")

        # NOT IN
        elif isinstance(expr, expression.notinExpression):
            self.sout.write(expr.field)
            self.sout.write(self.NOTINOP)
            self.sout.write("(")
            for count, item in enumerate(expr.text):
                if count != 0:
                    self.sout.write(", ")
                self.addArgument(item)
            self.sout.write(")")


    def generateSubExpression(self, expression):
        """
        Generate an SQL expression possibly in parenthesis if its a compound expression.

        @param expression: the L{baseExpression} to write out.
        @return: C{True} if the TIMESPAN table is used, C{False} otherwise.
        """

        if expression.multi():
            self.sout.write("(")
        self.generateExpression(expression)
        if expression.multi():
            self.sout.write(")")


    def addArgument(self, arg):
        """

        @param arg: the C{str} of the argument to add
        """

        # Append argument to the list and add the appropriate substitution string to the output stream.
        self.arguments.append(arg)
        self.substitutions.append(":" + str(len(self.arguments)))
        self.sout.write("%s")


    def setArgument(self, arg):
        """

        @param arg: the C{str} of the argument to add
        @return: C{str} for argument substitution text
        """

        # Append argument to the list and add the appropriate substitution string to the output stream.
        self.arguments.append(arg)
        self.substitutions.append(":" + str(len(self.arguments)))


    def frontArgument(self, arg):
        """

        @param arg: the C{str} of the argument to add
        @return: C{str} for argument substitution text
        """

        # Append argument to the list and add the appropriate substitution string to the output stream.
        self.arguments.insert(0, arg)
        self.substitutions.append(":" + str(len(self.arguments)))


    def containsArgument(self, arg):
        return "*%s*" % (arg,)


    def startswithArgument(self, arg):
        return "%s*" % (arg,)


    def endswithArgument(self, arg):
        return "*%s" % (arg,)
