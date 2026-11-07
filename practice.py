from PyQt5.QtWidgets import QApplication, QMainWindow, QPushButton, QWidget

# Only needed for access to command line arguments
import sys

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Practice App")
        self.setMinimumSize(300, 200)
        self.button = QPushButton("Click Me")
        self.button.clicked.connect(self.on_button_clicked)
        self.setCentralWidget(self.button)

    def on_button_clicked(self):
        self.button.setText("You already clicked me.")
        self.button.setEnabled(False)

        # Also change the window title.
        self.setWindowTitle("My Oneshot App")


#only one instance per application
app = QApplication(sys.argv)

#create main window
window = MainWindow()
window.show() # IMPORTANT!!!!! Windows are hidden by default.

# Start the event loop.
app.exec()