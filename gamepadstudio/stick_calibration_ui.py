"""A short, explicit software-deadzone measurement using fresh input frames."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
                              QLabel, QProgressBar, QPushButton, QVBoxLayout)

from .i18n import tr, tr_profile
from .stick_calibration import StickCalibration, supports_right_stick
from .studio_core import pointer_input_signature, profile_scope
from .test_widgets import StickGauge


def caption(text, kind='caption'):
    widget = QLabel(text)
    widget.setObjectName(kind)
    widget.setWordWrap(True)
    widget.setMinimumWidth(0)
    return widget


class StickMeasurementDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.identity = self.device_identity(owner.snapshot)
        self.input_signature = pointer_input_signature(owner.snapshot)
        self.invalidated = False
        self.calibration = StickCalibration()
        self.disconnected = False
        self.offline_signal = None
        self.setWindowTitle(tr('右摇杆静止测量'))
        self.resize(490, 510)
        self.setMinimumWidth(350)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(caption(tr('右摇杆静止测量'), 'heading'))
        layout.addWidget(caption((owner.snapshot or {}).get('name', tr('未连接手柄'))))
        layout.addWidget(caption(tr('把手柄放稳并松开右摇杆，测量期间不要触碰。')))
        self.gauge = StickGauge(drift=True)
        self.gauge.setFixedSize(150, 150)
        layout.addWidget(self.gauge, 0, Qt.AlignHCenter)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(5)
        layout.addWidget(self.progress_bar)
        self.message = caption(tr('准备好后开始测量。'))
        layout.addWidget(self.message)
        self.result_label = caption('', 'section')
        layout.addWidget(self.result_label)
        self.measure_button = QPushButton(tr('开始测量'))
        self.measure_button.clicked.connect(self.start)
        layout.addWidget(self.measure_button, 0, Qt.AlignLeft)
        layout.addStretch(1)
        layout.addWidget(caption(tr('应用到键鼠预设')))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(0)
        self.profile_combo.setAccessibleName(tr('应用到键鼠预设'))
        names = owner.store.profiles_for(owner.snapshot, mode='kbm')
        for name in names:
            self.profile_combo.addItem(tr_profile(name), name)
        current = owner.config.get('active_profile')
        if current in names:
            self.profile_combo.setCurrentIndex(names.index(current))
        self.profile_combo.currentIndexChanged.connect(self.check_device)
        layout.addWidget(self.profile_combo)
        layout.addWidget(caption(tr('仅修改所选预设的居中容错；这是软件补偿，不修改手柄固件。')))
        self.buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close)
        self.apply_button = self.buttons.button(QDialogButtonBox.Apply)
        self.apply_button.setText(tr('应用建议'))
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.save)
        self.buttons.button(QDialogButtonBox.Close).setText(tr('关闭'))
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        if owner.remote:
            self.sample_signal = owner.client.event
            self.sample_signal.connect(self.agent_event)
            socket = getattr(owner.client, 'socket', None)
            if socket is not None:
                self.offline_signal = socket.disconnected
                self.offline_signal.connect(self.backend_disconnected)
        else:
            self.sample_signal = owner.device_sample
            self.sample_signal.connect(self.on_sample)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(50)
        self.check_device()

    @staticmethod
    def device_identity(state):
        return (profile_scope(state), state.get('instance_id')) if state else None

    def check_device(self, *_):
        if self.identity is None or self.device_identity(self.owner.snapshot) != self.identity:
            self.invalidated = True
        if not supports_right_stick(self.owner.snapshot):
            self.invalidated = True
        elif pointer_input_signature(self.owner.snapshot) != self.input_signature:
            self.invalidated = True
        if self.owner.remote and not self.owner.client.connected:
            self.invalidated = True
        if not hasattr(self, 'apply_button'):
            return False
        if self.invalidated:
            self.message.setText(tr('设备或输入能力已变化，请重新打开测量。'))
        result = self.calibration.result
        target = self.profile_combo.currentData()
        valid_target = target in self.owner.store.profiles_for(self.owner.snapshot, mode='kbm')
        ready = (not self.invalidated and self.calibration.phase == 'complete'
                 and result is not None and result.get('recommended_deadzone') is not None
                 and valid_target)
        self.apply_button.setEnabled(ready)
        self.measure_button.setEnabled(not self.invalidated and self.calibration.phase not in ('settling', 'sampling'))
        return ready

    def start(self):
        self.check_device()
        if self.invalidated:
            return
        self.result_label.clear()
        self.render(self.calibration.start(self.owner.snapshot, time.monotonic()))

    def agent_event(self, message):
        if message.get('type') == 'state':
            self.on_sample(message.get('device'))

    def backend_disconnected(self):
        # Record a transport interruption even if reconnection happens before
        # the next timer tick and presents the same physical device instance.
        self.invalidated = True
        self.check_device()

    def on_sample(self, state):
        # Signal delivery is one real standalone read or one new backend frame.
        # A UI timer never re-samples its cached snapshot.
        if (self.device_identity(state) != self.identity or not supports_right_stick(state)
                or pointer_input_signature(state) != self.input_signature):
            self.invalidated = True
        if not self.invalidated and state:
            self.gauge.set_position((state['axes'][2], state['axes'][3]))
            if self.calibration.phase in ('settling', 'sampling'):
                self.render(self.calibration.sample(state, time.monotonic()))
        self.check_device()

    def tick(self):
        self.check_device()
        if not self.invalidated and self.calibration.phase in ('settling', 'sampling'):
            self.render(self.calibration.tick(time.monotonic()))

    def render(self, status):
        self.progress_bar.setValue(round(status['progress'] * 100))
        phase = status['phase']
        if phase == 'settling':
            self.message.setText(tr('正在等待摇杆静止…'))
        elif phase == 'sampling':
            self.message.setText(tr('正在测量，请保持松手…'))
        elif phase == 'failed':
            self.message.setText(tr(status['error']))
        elif phase == 'complete':
            result = status['result']
            self.message.setText(tr('测量完成 · 偏移 {offset}% · 波动 {noise}%',
                                    offset=f"{result['offset'] * 100:.1f}", noise=f"{result['noise'] * 100:.1f}"))
            recommended = result.get('recommended_deadzone')
            if recommended is not None:
                self.result_label.setText(tr('建议居中容错 {value}%', value=round(recommended * 100)))
            else:
                self.result_label.setText(tr(status['error']))
        if phase in ('complete', 'failed'):
            self.measure_button.setText(tr('重新测量'))
        self.check_device()

    def save(self):
        if not self.check_device():
            return
        change = {'op': 'pointer_deadzone', 'profile': self.profile_combo.currentData(),
                  'deadzone': self.calibration.result['recommended_deadzone'],
                  'expected_input_signature': self.input_signature}
        if self.owner.mapping_change(change):
            self.accept()
        else:
            notice = getattr(self.owner, 'notice', None)
            self.message.setText(notice.text() if notice is not None else tr('应用失败，预设未更改。'))

    def disconnect_samples(self):
        self.timer.stop()
        if not self.disconnected:
            self.sample_signal.disconnect(self.agent_event if self.owner.remote else self.on_sample)
            if self.offline_signal is not None:
                self.offline_signal.disconnect(self.backend_disconnected)
            self.disconnected = True

    def done(self, result):
        self.disconnect_samples()
        super().done(result)

    def closeEvent(self, event):
        self.disconnect_samples()
        super().closeEvent(event)
