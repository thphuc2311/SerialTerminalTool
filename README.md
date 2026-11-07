Vibe coding Serial Terminal Tool <- currently
Add Tab USB for custom communication protocol from Terminal to Master and vice versa <- future
Next step at current stage:
- Check the USB frame/package is correct the requirement.
- Check the fault frame/package indicator
- Find solution to stream data smoothly
- Consider dynamic frame length with CAN ID to identify each data slave node
- Consider simplify mechanism request data from slave each 10ms.
- Consider apply monitoring all slave Rx TimeOut (should split monitoring TimeOut code to function)

Install:
pip install PyQt5 pyserial
pip install pyinstaller -> pakage to .exe

To build exe:
pyinstaller --noconfirm --onefile --windowed --name SerialTerminal SerialTerminal.py 

trong đó:
--onefile: góm tất cả vào 1 file .exe duy nhất. 
--windowed: không hiện cửa sổ console đen phía sau (vì đây là ứng dụng GUI).
--name: đặt tên file xuất ra.

Nếu muốn icon riêng cho file exe, thêm --icon=duong_dan_file.ico vào lệnh build.

+ Giải thích cờ --onefile
--onefile là cờ của PyInstaller quyết định cách đóng gói kết quả build.

+ Có --onefile:
Toàn bộ interpreter Python, các thư viện (PyQt5, pyserial...) và code của bạn được nén gộp vào một file .exe duy nhất.
Khi bạn chạy file exe này, nó tự giải nén mọi thứ ra một thư mục tạm (thường ở %TEMP%\_MEIxxxxxx), rồi mới chạy chương trình thật từ đó.
Giải nén xong nó tự xoá đi khi đóng app (trừ một số trường hợp bị treo lại nếu app bị crash).
Ưu điểm: gọn, chỉ cần gửi 1 file duy nhất cho người khác.
Nhược điểm: khởi động chậm hơn mỗi lần mở (vài trăm ms đến vài giây tùy máy và kích thước app, vì phải giải nén lại từ đầu mỗi lần chạy).

+ Không có --onefile (mặc định --onedir):
Kết quả build là một thư mục (dist\SerialTerminal\) chứa file SerialTerminal.exe cùng rất nhiều file .dll, .pyd đi kèm.
Chạy nhanh hơn vì không cần giải nén — mọi thứ đã nằm sẵn trên đĩa.
Nhược điểm: phải gửi/copy cả thư mục cho người khác, không gọn bằng 1 file.

Version info:
v1: only serial view
v2: update USB frame view