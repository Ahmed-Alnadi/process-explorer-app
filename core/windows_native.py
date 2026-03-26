import ctypes
from ctypes import wintypes


ERROR_MORE_DATA = 234
IF_MAX_STRING_SIZE = 256
IF_MAX_PHYS_ADDRESS_LENGTH = 32
IF_OPER_STATUS_UP = 1
PDH_FMT_DOUBLE = 0x00000200
PDH_MORE_DATA = 0x800007D2
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SC_MANAGER_CONNECT = 0x0001
SERVICE_ENUMERATE_DEPENDENTS = 0x0008
SERVICE_STATE_ALL = 0x00000003

SERVICE_STATE_NAMES = {
    1: "Stopped",
    2: "Start Pending",
    3: "Stop Pending",
    4: "Running",
    5: "Continue Pending",
    6: "Pause Pending",
    7: "Paused",
}


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class SERVICE_STATUS(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", wintypes.DWORD),
        ("dwCurrentState", wintypes.DWORD),
        ("dwControlsAccepted", wintypes.DWORD),
        ("dwWin32ExitCode", wintypes.DWORD),
        ("dwServiceSpecificExitCode", wintypes.DWORD),
        ("dwCheckPoint", wintypes.DWORD),
        ("dwWaitHint", wintypes.DWORD),
    ]


class ENUM_SERVICE_STATUSW(ctypes.Structure):
    _fields_ = [
        ("lpServiceName", wintypes.LPWSTR),
        ("lpDisplayName", wintypes.LPWSTR),
        ("ServiceStatus", SERVICE_STATUS),
    ]


class MIB_IF_ROW2(ctypes.Structure):
    _fields_ = [
        ("InterfaceLuid", ctypes.c_ulonglong),
        ("InterfaceIndex", wintypes.DWORD),
        ("InterfaceGuid", ctypes.c_byte * 16),
        ("Alias", wintypes.WCHAR * (IF_MAX_STRING_SIZE + 1)),
        ("Description", wintypes.WCHAR * (IF_MAX_STRING_SIZE + 1)),
        ("PhysicalAddressLength", wintypes.DWORD),
        ("PhysicalAddress", ctypes.c_ubyte * IF_MAX_PHYS_ADDRESS_LENGTH),
        ("PermanentPhysicalAddress", ctypes.c_ubyte * IF_MAX_PHYS_ADDRESS_LENGTH),
        ("Mtu", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TunnelType", ctypes.c_int),
        ("MediaType", ctypes.c_int),
        ("PhysicalMediumType", ctypes.c_int),
        ("AccessType", ctypes.c_int),
        ("DirectionType", ctypes.c_int),
        ("InterfaceAndOperStatusFlags", ctypes.c_ubyte),
        ("OperStatus", ctypes.c_int),
        ("AdminStatus", ctypes.c_int),
        ("MediaConnectState", ctypes.c_int),
        ("NetworkGuid", ctypes.c_byte * 16),
        ("ConnectionType", ctypes.c_int),
        ("TransmitLinkSpeed", ctypes.c_ulonglong),
        ("ReceiveLinkSpeed", ctypes.c_ulonglong),
        ("InOctets", ctypes.c_ulonglong),
        ("InUcastPkts", ctypes.c_ulonglong),
        ("InNUcastPkts", ctypes.c_ulonglong),
        ("InDiscards", ctypes.c_ulonglong),
        ("InErrors", ctypes.c_ulonglong),
        ("InUnknownProtos", ctypes.c_ulonglong),
        ("InUcastOctets", ctypes.c_ulonglong),
        ("InMulticastOctets", ctypes.c_ulonglong),
        ("InBroadcastOctets", ctypes.c_ulonglong),
        ("OutOctets", ctypes.c_ulonglong),
        ("OutUcastPkts", ctypes.c_ulonglong),
        ("OutNUcastPkts", ctypes.c_ulonglong),
        ("OutDiscards", ctypes.c_ulonglong),
        ("OutErrors", ctypes.c_ulonglong),
        ("OutUcastOctets", ctypes.c_ulonglong),
        ("OutMulticastOctets", ctypes.c_ulonglong),
        ("OutBroadcastOctets", ctypes.c_ulonglong),
        ("OutQLen", ctypes.c_ulonglong),
    ]


class MIB_IF_TABLE2(ctypes.Structure):
    _fields_ = [
        ("NumEntries", wintypes.ULONG),
        ("Table", MIB_IF_ROW2 * 1),
    ]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


class PDH_FMT_COUNTERVALUE_UNION(ctypes.Union):
    _fields_ = [
        ("longValue", wintypes.LONG),
        ("doubleValue", ctypes.c_double),
        ("largeValue", ctypes.c_longlong),
    ]


class PDH_FMT_COUNTERVALUE(ctypes.Structure):
    _fields_ = [
        ("CStatus", wintypes.DWORD),
        ("unionValue", PDH_FMT_COUNTERVALUE_UNION),
    ]


class PDH_FMT_COUNTERVALUE_ITEM_W(ctypes.Structure):
    _fields_ = [
        ("szName", wintypes.LPWSTR),
        ("FmtValue", PDH_FMT_COUNTERVALUE),
    ]


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def native_memory_status():
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return {
            "memory_load_percent": float(status.dwMemoryLoad),
            "total_phys": int(status.ullTotalPhys),
            "avail_phys": int(status.ullAvailPhys),
            "used_phys": int(status.ullTotalPhys - status.ullAvailPhys),
        }
    except Exception:
        return None


def native_uptime_seconds():
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        return float(kernel32.GetTickCount64() / 1000.0)
    except Exception:
        return None


def native_process_private_bytes(pid):
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
    except Exception:
        return None

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    process_handle = None
    try:
        access = PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_QUERY_INFORMATION
        process_handle = kernel32.OpenProcess(access, False, int(pid))
        if not process_handle:
            return None
        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
        if not psapi.GetProcessMemoryInfo(
            process_handle,
            ctypes.byref(counters),
            counters.cb,
        ):
            return None
        return int(counters.PrivateUsage)
    except Exception:
        return None
    finally:
        if process_handle:
            kernel32.CloseHandle(process_handle)


def native_network_adapters():
    try:
        iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
    except Exception:
        return []

    GetIfTable2 = iphlpapi.GetIfTable2
    GetIfTable2.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    GetIfTable2.restype = wintypes.ULONG
    FreeMibTable = iphlpapi.FreeMibTable
    FreeMibTable.argtypes = [ctypes.c_void_p]
    FreeMibTable.restype = None

    table_ptr = ctypes.c_void_p()
    result = GetIfTable2(ctypes.byref(table_ptr))
    if result != 0 or not table_ptr.value:
        return []

    adapters = []
    try:
        table = ctypes.cast(table_ptr, ctypes.POINTER(MIB_IF_TABLE2)).contents
        base_address = ctypes.addressof(table.Table)
        row_size = ctypes.sizeof(MIB_IF_ROW2)
        for index in range(int(table.NumEntries)):
            row = ctypes.cast(
                base_address + (index * row_size),
                ctypes.POINTER(MIB_IF_ROW2),
            ).contents
            alias = (row.Alias or "").strip()
            description = (row.Description or "").strip()
            if not alias and not description:
                continue
            adapters.append(
                {
                    "index": int(row.InterfaceIndex),
                    "alias": alias or description,
                    "description": description or alias,
                    "is_up": int(row.OperStatus) == IF_OPER_STATUS_UP,
                    "rx_bytes": int(row.InOctets),
                    "tx_bytes": int(row.OutOctets),
                    "rx_link_speed": int(row.ReceiveLinkSpeed),
                    "tx_link_speed": int(row.TransmitLinkSpeed),
                }
            )
    except Exception:
        return []
    finally:
        FreeMibTable(table_ptr)

    adapters.sort(key=lambda item: (0 if item["is_up"] else 1, item["alias"].lower()))
    return adapters


def native_display_adapters():
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
    except Exception:
        return []

    EnumDisplayDevicesW = user32.EnumDisplayDevicesW
    EnumDisplayDevicesW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(DISPLAY_DEVICEW),
        wintypes.DWORD,
    ]
    EnumDisplayDevicesW.restype = wintypes.BOOL

    adapters = []
    index = 0
    while True:
        device = DISPLAY_DEVICEW()
        device.cb = ctypes.sizeof(DISPLAY_DEVICEW)
        if not EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
            break
        name = (device.DeviceString or "").strip()
        if name:
            adapters.append(name)
        index += 1
    return adapters


class NativeGpuUsageMonitor:
    def __init__(self):
        self._pdh = None
        self._query = wintypes.HANDLE()
        self._counter = wintypes.HANDLE()
        self._ready = False
        self._initialize()

    def _initialize(self):
        try:
            self._pdh = ctypes.WinDLL("pdh", use_last_error=True)
        except Exception:
            return

        self._pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(wintypes.HANDLE)]
        self._pdh.PdhOpenQueryW.restype = wintypes.DWORD
        self._pdh.PdhAddEnglishCounterW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        self._pdh.PdhAddEnglishCounterW.restype = wintypes.DWORD
        self._pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
        self._pdh.PdhCollectQueryData.restype = wintypes.DWORD
        self._pdh.PdhGetFormattedCounterArrayW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        self._pdh.PdhGetFormattedCounterArrayW.restype = wintypes.DWORD
        self._pdh.PdhCloseQuery.argtypes = [wintypes.HANDLE]
        self._pdh.PdhCloseQuery.restype = wintypes.DWORD

        if self._pdh.PdhOpenQueryW(None, None, ctypes.byref(self._query)) != 0:
            return

        path = "\\GPU Engine(*)\\Utilization Percentage"
        if self._pdh.PdhAddEnglishCounterW(self._query, path, None, ctypes.byref(self._counter)) != 0:
            self._pdh.PdhCloseQuery(self._query)
            self._query = wintypes.HANDLE()
            return

        self._pdh.PdhCollectQueryData(self._query)
        self._ready = True

    def close(self):
        if self._pdh is not None and self._query:
            try:
                self._pdh.PdhCloseQuery(self._query)
            except Exception:
                pass
        self._ready = False

    def read(self):
        if not self._ready:
            return None

        try:
            self._pdh.PdhCollectQueryData(self._query)
            buffer_size = wintypes.DWORD(0)
            item_count = wintypes.DWORD(0)
            status = self._pdh.PdhGetFormattedCounterArrayW(
                self._counter,
                PDH_FMT_DOUBLE,
                ctypes.byref(buffer_size),
                ctypes.byref(item_count),
                None,
            )
            if status not in (0, PDH_MORE_DATA) or buffer_size.value <= 0 or item_count.value <= 0:
                return None

            buffer = ctypes.create_string_buffer(buffer_size.value)
            status = self._pdh.PdhGetFormattedCounterArrayW(
                self._counter,
                PDH_FMT_DOUBLE,
                ctypes.byref(buffer_size),
                ctypes.byref(item_count),
                buffer,
            )
            if status != 0:
                return None

            array_type = PDH_FMT_COUNTERVALUE_ITEM_W * item_count.value
            items = ctypes.cast(buffer, ctypes.POINTER(array_type)).contents
            engines = []
            busiest_percent = 0.0
            busiest_name = "N/A"
            for item in items:
                instance_name = (item.szName or "").strip()
                if not instance_name or "engtype_idle" in instance_name.lower():
                    continue
                value = float(item.FmtValue.unionValue.doubleValue)
                if value < 0:
                    continue
                clamped = max(0.0, min(value, 100.0))
                engines.append({"name": instance_name, "percent": clamped})
                if clamped >= busiest_percent:
                    busiest_percent = clamped
                    busiest_name = instance_name

            engines.sort(key=lambda engine: engine["percent"], reverse=True)
            return {
                "busiest_percent": busiest_percent,
                "busiest_name": busiest_name,
                "engines": engines[:6],
                "adapters": native_display_adapters(),
            }
        except Exception:
            return None


class NativeCpuUsageMonitor:
    def __init__(self):
        self._pdh = None
        self._query = wintypes.HANDLE()
        self._counter = wintypes.HANDLE()
        self._ready = False
        self._initialize()

    def _initialize(self):
        try:
            self._pdh = ctypes.WinDLL("pdh", use_last_error=True)
        except Exception:
            return

        self._pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(wintypes.HANDLE)]
        self._pdh.PdhOpenQueryW.restype = wintypes.DWORD
        self._pdh.PdhAddEnglishCounterW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        self._pdh.PdhAddEnglishCounterW.restype = wintypes.DWORD
        self._pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
        self._pdh.PdhCollectQueryData.restype = wintypes.DWORD
        self._pdh.PdhGetFormattedCounterValue.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(PDH_FMT_COUNTERVALUE),
        ]
        self._pdh.PdhGetFormattedCounterValue.restype = wintypes.DWORD
        self._pdh.PdhCloseQuery.argtypes = [wintypes.HANDLE]
        self._pdh.PdhCloseQuery.restype = wintypes.DWORD

        if self._pdh.PdhOpenQueryW(None, None, ctypes.byref(self._query)) != 0:
            return

        for path in (
            "\\Processor Information(_Total)\\% Processor Utility",
            "\\Processor(_Total)\\% Processor Time",
        ):
            if self._pdh.PdhAddEnglishCounterW(self._query, path, None, ctypes.byref(self._counter)) == 0:
                self._pdh.PdhCollectQueryData(self._query)
                self._ready = True
                return

        self._pdh.PdhCloseQuery(self._query)
        self._query = wintypes.HANDLE()

    def close(self):
        if self._pdh is not None and self._query:
            try:
                self._pdh.PdhCloseQuery(self._query)
            except Exception:
                pass
        self._ready = False

    def read_percent(self):
        if not self._ready:
            return None
        try:
            if self._pdh.PdhCollectQueryData(self._query) != 0:
                return None
            counter_type = wintypes.DWORD(0)
            value = PDH_FMT_COUNTERVALUE()
            if self._pdh.PdhGetFormattedCounterValue(
                self._counter,
                PDH_FMT_DOUBLE,
                ctypes.byref(counter_type),
                ctypes.byref(value),
            ) != 0:
                return None
            return max(0.0, min(float(value.unionValue.doubleValue), 100.0))
        except Exception:
            return None


class NativeDiskActivityMonitor:
    def __init__(self):
        self._pdh = None
        self._query = wintypes.HANDLE()
        self._counter = wintypes.HANDLE()
        self._ready = False
        self._initialize()

    def _initialize(self):
        try:
            self._pdh = ctypes.WinDLL("pdh", use_last_error=True)
        except Exception:
            return

        self._pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(wintypes.HANDLE)]
        self._pdh.PdhOpenQueryW.restype = wintypes.DWORD
        self._pdh.PdhAddEnglishCounterW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        self._pdh.PdhAddEnglishCounterW.restype = wintypes.DWORD
        self._pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
        self._pdh.PdhCollectQueryData.restype = wintypes.DWORD
        self._pdh.PdhGetFormattedCounterValue.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(PDH_FMT_COUNTERVALUE),
        ]
        self._pdh.PdhGetFormattedCounterValue.restype = wintypes.DWORD
        self._pdh.PdhCloseQuery.argtypes = [wintypes.HANDLE]
        self._pdh.PdhCloseQuery.restype = wintypes.DWORD

        if self._pdh.PdhOpenQueryW(None, None, ctypes.byref(self._query)) != 0:
            return

        path = "\\PhysicalDisk(_Total)\\% Idle Time"
        if self._pdh.PdhAddEnglishCounterW(self._query, path, None, ctypes.byref(self._counter)) != 0:
            self._pdh.PdhCloseQuery(self._query)
            self._query = wintypes.HANDLE()
            return

        self._pdh.PdhCollectQueryData(self._query)
        self._ready = True

    def close(self):
        if self._pdh is not None and self._query:
            try:
                self._pdh.PdhCloseQuery(self._query)
            except Exception:
                pass
        self._ready = False

    def read_active_percent(self):
        if not self._ready:
            return None

        try:
            status = self._pdh.PdhCollectQueryData(self._query)
            if status != 0:
                return None
            counter_type = wintypes.DWORD(0)
            value = PDH_FMT_COUNTERVALUE()
            status = self._pdh.PdhGetFormattedCounterValue(
                self._counter,
                PDH_FMT_DOUBLE,
                ctypes.byref(counter_type),
                ctypes.byref(value),
            )
            if status != 0:
                return None
            idle_percent = max(0.0, min(float(value.unionValue.doubleValue), 100.0))
            return max(0.0, 100.0 - idle_percent)
        except Exception:
            return None


def native_dependent_services(service_name):
    if not service_name:
        return []

    try:
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    except Exception:
        return []

    advapi32.OpenSCManagerW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
    ]
    advapi32.OpenSCManagerW.restype = wintypes.HANDLE
    advapi32.OpenServiceW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.DWORD,
    ]
    advapi32.OpenServiceW.restype = wintypes.HANDLE
    advapi32.CloseServiceHandle.argtypes = [wintypes.HANDLE]
    advapi32.CloseServiceHandle.restype = wintypes.BOOL
    advapi32.EnumDependentServicesW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPBYTE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.EnumDependentServicesW.restype = wintypes.BOOL

    scm_handle = None
    service_handle = None
    try:
        scm_handle = advapi32.OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
        if not scm_handle:
            return []

        service_handle = advapi32.OpenServiceW(
            scm_handle,
            service_name,
            SERVICE_ENUMERATE_DEPENDENTS,
        )
        if not service_handle:
            return []

        bytes_needed = wintypes.DWORD(0)
        services_returned = wintypes.DWORD(0)
        ok = advapi32.EnumDependentServicesW(
            service_handle,
            SERVICE_STATE_ALL,
            None,
            0,
            ctypes.byref(bytes_needed),
            ctypes.byref(services_returned),
        )
        if ok and services_returned.value == 0:
            return []

        error_code = ctypes.get_last_error()
        if not ok and error_code not in (0, ERROR_MORE_DATA):
            return []
        if bytes_needed.value <= 0:
            return []

        buffer = ctypes.create_string_buffer(bytes_needed.value)
        bytes_needed = wintypes.DWORD(bytes_needed.value)
        services_returned = wintypes.DWORD(0)
        ok = advapi32.EnumDependentServicesW(
            service_handle,
            SERVICE_STATE_ALL,
            ctypes.cast(buffer, wintypes.LPBYTE),
            bytes_needed.value,
            ctypes.byref(bytes_needed),
            ctypes.byref(services_returned),
        )
        if not ok or services_returned.value <= 0:
            return []

        array_type = ENUM_SERVICE_STATUSW * services_returned.value
        services = ctypes.cast(buffer, ctypes.POINTER(array_type)).contents
        dependents = []
        for service in services:
            dependents.append(
                {
                    "name": service.lpServiceName or "",
                    "display_name": service.lpDisplayName or service.lpServiceName or "",
                    "status": SERVICE_STATE_NAMES.get(
                        int(service.ServiceStatus.dwCurrentState),
                        "Unknown",
                    ),
                }
            )
        return dependents
    except Exception:
        return []
    finally:
        if service_handle:
            advapi32.CloseServiceHandle(service_handle)
        if scm_handle:
            advapi32.CloseServiceHandle(scm_handle)
