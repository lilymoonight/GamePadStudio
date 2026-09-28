"""Resolution-independent controller illustration and real-time input visualizer."""
from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QLinearGradient, QRadialGradient, QPen, QFont
from PySide6.QtWidgets import QWidget
from .controller_catalog import CATALOG, button_labels


class ControllerArt(QWidget):
    button_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(440, 270)
        self.buttons = set()
        self.axes = [0.] * 6
        self.led = '#5686ff'
        self.family='dualsense';self.interactive=True;self.available=None
        self.set_family('dualsense')

    def set_family(self,family):
        self.family=family if family in CATALOG else 'generic'
        self.points = {0: (488, 180), 1: (519, 148), 2: (457, 148), 3: (488, 116),
                       4: (232, 116), 5: (320, 227), 6: (408, 116),
                       7: (260, 214), 8: (380, 214), 9: (174, 69), 10: (466, 69),
                       11: (151, 125), 12: (151, 176), 13: (126, 151), 14: (176, 151),
                       15: (320, 252), 20: (320, 127)}
        if CATALOG[self.family]['layout']=='offset':
            self.points.update({7:(160,145),11:(260,191),12:(260,238),13:(237,214),14:(283,214),
                                4:(280,136),5:(320,108),6:(360,136),15:(320,182)})
            self.points.pop(20,None)
        elif self.family=='dualshock4':self.points.pop(15,None)
        self.update()

    def update_state(self, state):
        if state and state.get('family',self.family)!=self.family:self.set_family(state['family'])
        self.available=set(state['available_buttons']) if state and 'available_buttons' in state else None
        self.buttons = set(state['buttons']) if state else set()
        self.axes = state['axes'] if state else [0.] * 6
        self.update()

    def mousePressEvent(self, event):
        if not self.interactive:return
        scale = min(self.width()/640, self.height()/365)
        x = (event.position().x()-(self.width()-640*scale)/2)/scale
        y = (event.position().y()-(self.height()-365*scale)/2)/scale
        candidates={k:v for k,v in self.points.items() if self.available is None or k in self.available}
        if not candidates:return
        near = min(candidates, key=lambda b: (self.points[b][0]-x)**2+(self.points[b][1]-y)**2)
        if (self.points[near][0]-x)**2+(self.points[near][1]-y)**2 < 40**2:
            self.button_clicked.emit(near)

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        scale = min(self.width()/640, self.height()/365)
        p.translate((self.width()-640*scale)/2, (self.height()-365*scale)/2); p.scale(scale, scale)
        if CATALOG[self.family]['layout']=='offset':
            self.paint_offset(p);p.end();return
        glow = QRadialGradient(320, 188, 290)
        glow.setColorAt(0, QColor(64, 104, 232, 36)); glow.setColorAt(1, QColor(15, 20, 31, 0))
        p.setPen(Qt.NoPen); p.setBrush(glow); p.drawEllipse(QRectF(35, 2, 570, 350))
        p.setBrush(QColor(3, 7, 17, 85)); p.drawEllipse(QRectF(95, 294, 450, 33))
        for x, button, trigger in [(134, 9, self.axes[4]), (426, 10, self.axes[5])]:
            p.setBrush(QColor('#303949')); p.drawRoundedRect(QRectF(x, 45, 80, 47), 18, 18)
            p.setBrush(QColor('#81a3ff') if button in self.buttons else QColor('#697586'))
            p.drawRoundedRect(QRectF(x+3, 61, 74, 18), 8, 8)
            p.setBrush(QColor('#5686ff')); p.drawRoundedRect(QRectF(x+5, 47, max(0., trigger)*70, 5), 2, 2)
        body = QPainterPath(); body.moveTo(171, 75)
        body.cubicTo(124, 69, 112, 102, 97, 144); body.cubicTo(78, 198, 65, 249, 79, 291)
        body.cubicTo(88, 320, 117, 320, 141, 288); body.lineTo(198, 225)
        body.cubicTo(250, 253, 390, 253, 442, 225); body.lineTo(499, 288)
        body.cubicTo(523, 320, 552, 320, 561, 291); body.cubicTo(575, 249, 562, 198, 543, 144)
        body.cubicTo(528, 102, 516, 69, 469, 75); body.cubicTo(385, 57, 255, 57, 171, 75)
        shell = QLinearGradient(0, 64, 0, 315)
        dark=self.family=='dualshock4'
        shell.setColorAt(0, QColor('#657088' if dark else '#fafbff')); shell.setColorAt(.48, QColor('#394255' if dark else '#d7dce5')); shell.setColorAt(1, QColor('#202838' if dark else '#8492a7'))
        p.setBrush(shell); p.setPen(QPen(QColor('#e4eaf5'), 1)); p.drawPath(body)
        inner = QPainterPath(); inner.moveTo(223, 85); inner.cubicTo(204, 122, 190, 164, 200, 214)
        inner.cubicTo(226, 264, 280, 255, 320, 269); inner.cubicTo(360, 255, 414, 264, 440, 214)
        inner.cubicTo(450, 164, 436, 122, 417, 85); inner.closeSubpath()
        p.setBrush(QColor('#171c27')); p.setPen(Qt.NoPen); p.drawPath(inner)
        p.setPen(QPen(QColor(self.led), 4)); p.drawLine(226, 90, 215, 157); p.drawLine(414, 90, 425, 157)
        pad = QLinearGradient(0, 79, 0, 173); pad.setColorAt(0, QColor('#505a71' if dark else '#e4e7ee')); pad.setColorAt(1, QColor('#333e54' if dark else '#aab5c6'))
        p.setPen(QPen(QColor('#7b8a9f'), 1)); p.setBrush(QColor('#90b0ff') if 20 in self.buttons else pad)
        p.drawRoundedRect(QRectF(242, 82, 156, 83), 18, 18)
        p.setPen(QPen(QColor(55, 72, 98, 36), 1))
        for x in range(253, 390, 7):
            for y in range(96, 154, 7): p.drawPoint(x, y)
        for index, x in [(7, 260), (8, 380)]:
            p.setPen(QPen(QColor('#080c14'), 3)); p.setBrush(QColor('#4b5669')); p.drawEllipse(QPointF(x, 214), 34, 34)
            a = 0 if index == 7 else 2
            center = QPointF(x+self.axes[a]*9, 214+self.axes[a+1]*9)
            p.setBrush(QColor('#456bce') if index in self.buttons else QColor('#242c3a'))
            p.setPen(QPen(QColor('#667185'), 2)); p.drawEllipse(center, 26, 26)
            p.setPen(QPen(QColor('#151b26'), 1)); p.drawEllipse(center, 20, 20)
        for b in (11, 12, 13, 14):
            x,y = self.points[b]; p.setPen(QPen(QColor('#727d91'), 1))
            p.setBrush(QColor('#5686ff') if b in self.buttons else QColor('#303a4b'))
            p.drawRoundedRect(QRectF(x-10, y-10, 20, 20), 4, 4)
        for b, color in [(0, '#659fed'), (1, '#ef8399'), (2, '#d79be7'), (3, '#6acdb2')]:
            x,y = self.points[b]; p.setBrush(QColor('#537cd7') if b in self.buttons else QColor('#d4dae4'))
            p.setPen(QPen(QColor('#9ba8bc'), 1)); p.drawEllipse(QPointF(x,y), 18,18)
            p.setPen(QPen(QColor(color), 2)); p.setBrush(Qt.NoBrush)
            if b == 0: p.drawLine(x-6,y-6,x+6,y+6); p.drawLine(x-6,y+6,x+6,y-6)
            if b == 1: p.drawEllipse(QPointF(x,y), 8,8)
            if b == 2: p.drawRect(QRectF(x-7,y-7,14,14))
            if b == 3:
                tri = QPainterPath(); tri.moveTo(x,y-8); tri.lineTo(x-8,y+7); tri.lineTo(x+8,y+7); tri.closeSubpath(); p.drawPath(tri)
        for b in (4, 6):
            x,y=self.points[b]; p.setPen(Qt.NoPen); p.setBrush(QColor('#5686ff') if b in self.buttons else QColor('#67768b'))
            p.drawRoundedRect(QRectF(x-3,y-10,6,20),3,3)
        p.setFont(QFont('Segoe UI', 10, QFont.Bold)); p.setPen(QColor('#72a1ff') if 5 in self.buttons else QColor('#c0c9d9'))
        p.drawText(QRectF(301, 208, 38, 30), Qt.AlignCenter, 'PS')
        p.setPen(Qt.NoPen); p.setBrush(QColor('#ffc776') if 15 in self.buttons else QColor('#7a8597'))
        if not dark:p.drawRoundedRect(QRectF(311,249,18,5),2,2)
        p.setPen(QColor('#71809a')); p.setFont(QFont('Segoe UI', 8))
        p.drawText(QRectF(90,331,460,22), Qt.AlignCenter, 'D U A L S H O C K  4' if dark else 'D U A L S E N S E')
        p.end()

    def paint_offset(self,p):
        accent=CATALOG[self.family]['accent']
        p.setPen(Qt.NoPen);p.setBrush(QColor(3,7,17,90));p.drawEllipse(QRectF(92,294,456,35))
        for x,b,axis in [(125,9,4),(425,10,5)]:
            p.setBrush(QColor('#485569'));p.drawRoundedRect(QRectF(x,59,88,38),12,12)
            p.setBrush(QColor(accent));p.drawRoundedRect(QRectF(x+8,61,max(0,self.axes[axis])*70,5),2,2)
        body=QPainterPath();body.moveTo(177,77);body.cubicTo(128,68,103,114,91,160)
        body.cubicTo(75,207,63,286,90,303);body.cubicTo(120,324,170,245,199,231)
        body.cubicTo(258,257,382,257,441,231);body.cubicTo(470,245,520,324,550,303)
        body.cubicTo(577,286,565,207,549,160);body.cubicTo(537,114,512,68,463,77)
        body.cubicTo(395,57,245,57,177,77)
        shell=QLinearGradient(0,65,0,310);shell.setColorAt(0,QColor('#596377'));shell.setColorAt(.35,QColor('#343e50'));shell.setColorAt(1,QColor('#171f2e'))
        p.setBrush(shell);p.setPen(QPen(QColor('#768297'),1.3));p.drawPath(body)
        for index,axis in [(7,0),(8,2)]:
            x,y=self.points[index];p.setPen(QPen(QColor('#141b27'),4));p.setBrush(QColor('#59677b'));p.drawEllipse(QPointF(x,y),36,36)
            center=QPointF(x+self.axes[axis]*9,y+self.axes[axis+1]*9)
            p.setBrush(QColor(accent) if index in self.buttons else QColor('#182332'));p.setPen(QPen(QColor('#8593a6'),2));p.drawEllipse(center,28,28)
            p.setPen(QPen(QColor('#354258'),2));p.drawEllipse(center,21,21)
        for b in (11,12,13,14):
            x,y=self.points[b];p.setPen(QPen(QColor('#67768b'),1));p.setBrush(QColor(accent) if b in self.buttons else QColor('#1a2535'));p.drawRoundedRect(QRectF(x-11,y-11,22,22),4,4)
        labels=button_labels(self.family)
        for b,color in [(0,'#83d58e'),(1,'#f69099'),(2,'#85b7ff'),(3,'#ead383')]:
            x,y=self.points[b];p.setBrush(QColor(accent) if b in self.buttons else QColor('#192534'));p.setPen(QPen(QColor('#748298'),1));p.drawEllipse(QPointF(x,y),20,20)
            p.setPen(QColor('#e0e7f2' if self.family=='switch' else color));p.setFont(QFont('Segoe UI',15,QFont.Bold));p.drawText(QRectF(x-20,y-20,40,40),Qt.AlignCenter,labels[b])
        for b,caption in [(4,'−' if self.family=='switch' else '▱'),(5,'H' if self.family=='switch' else 'X' if self.family=='xbox' else 'G'),(6,'+' if self.family=='switch' else '≡'),(15,'○')]:
            if self.available is not None and b not in self.available:continue
            x,y=self.points[b];p.setBrush(QColor(accent) if b in self.buttons else QColor('#243248'));p.setPen(QPen(QColor('#8190a7'),1));p.drawEllipse(QPointF(x,y),14,14)
            p.setPen(QColor('#e6eefc'));p.setFont(QFont('Segoe UI',11,QFont.Bold));p.drawText(QRectF(x-14,y-14,28,28),Qt.AlignCenter,caption)
        p.setPen(QColor('#8193b0'));p.setFont(QFont('Segoe UI',9));p.drawText(QRectF(90,330,460,22),Qt.AlignCenter,CATALOG[self.family]['brand'])
