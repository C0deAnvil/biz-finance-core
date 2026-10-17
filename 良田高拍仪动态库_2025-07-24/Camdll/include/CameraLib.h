// 下列 ifdef 块是创建使从 DLL 导出更简单的
// 宏的标准方法。此 DLL 中的所有文件都是用命令行上定义的 CameraLib_EXPORTS
// 符号编译的。在使用此 DLL 的
// 任何其他项目上不应定义此符号。这样，源文件中包含此文件的任何其他项目都会将
// CameraLib_API 函数视为是从 DLL 导入的，而此 DLL 则将用此宏定义的
// 符号视为是被导出的。
#ifdef CameraLib_EXPORTS
#define CameraLib_API __declspec(dllexport)
#else
#define CameraLib_API __declspec(dllimport)
#endif

extern "C" {
/// @brief 初始化动态库(必须首先调用此接口)
/// @param  
/// @return 1:成功; 0:失败
CameraLib_API int eInitLib(void);

//反初始化, 做一些销毁和释放的动作, 最后应该调用此接口
CameraLib_API void eUninitLib(void);

//获取摄像头个数
CameraLib_API int eGetCameraCount();

//打开摄像头
CameraLib_API int eOpenCamera(int iDevIndex, HWND hWnd);

//关闭摄像头
CameraLib_API void eCloseCamera(int iDevIndex);

//获取摄像头名称
CameraLib_API LPSTR eGetDevName(int iDevIndex);

//获取分辨率个数
CameraLib_API int eGetResCount(int iDevIndex);

//获取分辨率宽度
CameraLib_API int eGetResWidth(int iDevIndex, int iRes);

//获取分辨率高度
CameraLib_API int eGetResHeight(int iDevIndex, int iRes);

//拍照
CameraLib_API int eCapture(int iDevIndex, LPSTR szImagePath);

//开启自动纠偏
CameraLib_API void eEnableAutoCrop(int iDevIndex, bool bEnable);

// 对图片进行纠偏-20250207
CameraLib_API void eImageFileCrop(const char* fileName);

// 获取当前视频帧，纠偏区域坐标点-20250208
CameraLib_API void eCropArea(int& x1, int& y1, int& x2, int& y2, int& x3, int& y3, int& x4, int& y4);

//左转90度
CameraLib_API void eRotateLeft(int iDevIndex);

//右转90度
CameraLib_API void eRotateRight(int iDevIndex);

//设置视频回调函数
//typedef void (*PVOID)(char*);
CameraLib_API void eSetVideoCB(int iDev, PVOID pVideo);

/// @brief 设置视频回调函数
/// @param iDev 设备索引
/// @return 
/// typedef void (*PVOID)(UINT8* buffer, long size, int width, int height);
CameraLib_API void hSetVideoCB(int iDev, PVOID pVideo);

//设置颜色: 0--彩色, 1--灰度, 2--黑白
CameraLib_API void eSetDevColor(int iDevIndex, int iClrIndex);

//设定分辨率(先打开视频，后设置分辨率)
CameraLib_API void eSetResolution(int iDevIndex, int iRes);

//响应鼠标移动
CameraLib_API void eOnMouseMove(HWND hWnd, int x, int y);

//响应鼠标滚轮
CameraLib_API void eOnWheel(HWND hWnd, int zDelta);

//响应鼠标左键按下
CameraLib_API void eOnLMouseDown(HWND hWnd, int x, int y);

//响应鼠标左键弹起
CameraLib_API void eOnLMouseUp(HWND hWnd, int x, int y);

//设置鼠标模式, 0--拖动图像, 1--画框
CameraLib_API void eSetMouseMode(int iDevIndex, int iMode);

//将一组传入的图片路径转为pdf文件, 多个图片路径以"|"分隔
CameraLib_API void eMakePDF(LPCSTR imagespath, LPCSTR pdfpath);

//图片合成, szImagePath---多个图片的全路径以英文分号间隔, iMode--合成模式为0时行优先col有效, 
//为1时列优先row有效, row--行数, col--列数, nGap--间隙
CameraLib_API int eImagesMix(LPCSTR szImagePath, int iMode, int row, int col, int nGap, LPCSTR szSavePath);


//设置是否显示预览画面
CameraLib_API void eSetShowVideo(int iDev, bool show);


//设置显示视频的窗口句柄
CameraLib_API void eSetHWND(int iDev, HWND hWnd);


//设置扫码回车, flag-true单码模式,每个条码都要跟一个回车符, flag-false多码模式, 每个条码后边跟一个指定的字符
//sufix-多码模式时字符串最后是否追加字符, sufixch-追加的字符，有可能是回车，也可能是分隔符
CameraLib_API void eSetScanReturn(int iDev, bool flag, char ch, bool sufix, char sufixch);

//设置单码模式的扫码间隔, 单位毫秒
CameraLib_API void eSetScanIntervalTime(int iDev, int tm);

//获取摄像头属性值
CameraLib_API int eGetCameraControl(int iDev, int prop, int value);

//设置摄像头属性值
CameraLib_API void eSetCameraProp(int iDev, long val, int prop, bool isAuto);


//播放wav文件
CameraLib_API void ePlaySound(char* wavpath);


//根据索引获取摄像的PID
CameraLib_API LPCSTR eGetCameraPID(int iDevIndex);

//根据索引获取摄像头的VID
CameraLib_API LPCSTR eGetCameraVID(int iDevIndex);

// ====================  录像相关接口 begin ========================================================================
// ====================  录像相关接口 begin ========================================================================
// ====================  录像相关接口 begin ========================================================================

// 获取麦克风数量-20250210
CameraLib_API int eAudioGetCount();

// 获取麦克风名称-20250210
CameraLib_API const char* eAudioGetName(int i);

/* 录像设置-20250210
* iDev:摄像头索引
* header:是否添加片头
* headerImageName:片头图片
* posx:录像时间水印位置，x坐标
* posy:录像时间水印位置，y坐标
* color:录像时间水印文字颜色
* fontsize:水印字号（6~200）
* audioindex:录像音频索引
*/
CameraLib_API void eRecordSetInfo(int iDev, bool header, const char* headerImageName, int posx, int posy, COLORREF color, int fontsize, int audioindex);

/* 设置录像片头帧数-20250210
* iDev:摄像头索引
* headerframecount:帧数
*/
CameraLib_API void eRecordSetHeaderFrameCount(int iDev, int headerframecount);

/* 设置合成录像的合成方式-20250210
* iDev:摄像头索引
* mix:是否合成
* sub:
* mode:
*/
CameraLib_API void eRecordSetMixInfo(int iDev, bool mix, int sub, int mode);

/* 开始录像-20250210
* iDev:摄像头索引
* fileName:文件保存位置
* audio:是否录音
*/
CameraLib_API int eRecordStart(int iDev, const char* fileName, bool audio);

// 停止录像-20250210
CameraLib_API void eRecordStop(int iDev);

// ====================  录像相关接口 end ========================================================================
// ====================  录像相关接口 end ========================================================================
// ====================  录像相关接口 end ========================================================================


//识别图像中的条码
CameraLib_API LPCSTR eReadBarcodeEx(const char* fileName);

}
